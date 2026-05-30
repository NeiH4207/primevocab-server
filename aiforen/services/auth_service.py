"""Authentication service.

Responsibilities:
  * Verify Google ID tokens and upsert the matching `users` row.
  * Email/password sign-up + verify.
  * Issue access/refresh tokens with rotation.
  * Surface a unified user payload to the FE (matches what
    `useGoogleLogin.tsx` stores in localStorage).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core import security
from aiforen.core.config import get_settings
from aiforen.core.errors import Conflict, Unauthorized
from aiforen.repositories.pg.personalization import LearningPersonalizationRepo
from aiforen.repositories.pg.plans import SubscriptionRepo
from aiforen.repositories.pg.refresh_tokens import RefreshTokenRepo
from aiforen.repositories.pg.users import AuthProviderRepo, UserRepo

settings = get_settings()
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class AuthService:
    def __init__(self, session: AsyncSession):
        self.s = session
        self.users = UserRepo(session)
        self.providers = AuthProviderRepo(session)
        self.refreshes = RefreshTokenRepo(session)
        self.subs = SubscriptionRepo(session)

    # ------------------------------------------------------------------ #
    # Google
    # ------------------------------------------------------------------ #

    async def login_with_google(
        self,
        *,
        id_token: Optional[str] = None,
        access_token: Optional[str] = None,
        user_agent: Optional[str],
        ip: Optional[str],
    ) -> Dict[str, Any]:
        if id_token:
            info = await self._verify_google_token(id_token)
        elif access_token:
            info = await self._verify_google_access_token(access_token)
        else:
            raise Unauthorized("Missing Google token")
        google_id = info["sub"]
        email = info["email"].lower()
        name = info.get("name") or email.split("@")[0]
        picture = info.get("picture")

        provider = await self.providers.find("google", google_id)
        if provider:
            user = await self.users.get(provider.user_id)
        else:
            user = await self.users.by_email(email)
            if not user:
                user = await self.users.create(
                    email=email,
                    name=name,
                    avatar_url=picture,
                    email_verified=True,
                )
            await self.providers.link(
                user.id, "google", google_id, payload={"email": email}
            )
        if user is None:
            raise Unauthorized("Could not link Google account")

        # Backfill avatar if Google provides a fresher URL
        if picture and user.avatar_url != picture:
            await self.users.update_profile(user.id, avatar_url=picture)
            user.avatar_url = picture

        await self.users.touch_login(user.id)
        return await self._build_response(user.id, user_agent=user_agent, ip=ip)

    async def login_with_google_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        user_agent: Optional[str],
        ip: Optional[str],
    ) -> Dict[str, Any]:
        """Exchange OAuth authorization code (redirect UX) for app session tokens."""
        if not settings.google_client_id or not settings.google_client_secret:
            raise Unauthorized("Google sign-in is not configured")

        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
            if resp.status_code != 200:
                logger.warning("Google token exchange failed: {}", resp.text)
                raise Unauthorized("Invalid Google authorization code")
            token_payload = resp.json()
        except Unauthorized:
            raise
        except Exception as exc:
            raise Unauthorized(f"Google token exchange failed: {exc}") from exc

        id_token = token_payload.get("id_token")
        access_token = token_payload.get("access_token")
        if id_token:
            return await self.login_with_google(
                id_token=id_token, user_agent=user_agent, ip=ip
            )
        if access_token:
            return await self.login_with_google(
                access_token=access_token, user_agent=user_agent, ip=ip
            )
        raise Unauthorized("Google did not return tokens")

    @staticmethod
    async def _verify_google_access_token(access_token: str) -> Dict[str, Any]:
        """Validate OAuth access token from @react-oauth/google useGoogleLogin."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if resp.status_code != 200:
                raise Unauthorized("Invalid Google access token")
            info = resp.json()
        except Unauthorized:
            raise
        except Exception as exc:
            raise Unauthorized(f"Google userinfo failed: {exc}") from exc

        sub = info.get("sub")
        email = info.get("email")
        if not sub or not email:
            raise Unauthorized("Google account missing sub or email")
        return {
            "sub": sub,
            "email": email,
            "name": info.get("name"),
            "picture": info.get("picture"),
        }

    @staticmethod
    async def _verify_google_token(id_token_str: str) -> Dict[str, Any]:
        """Returns the verified payload.  Falls back to *unverified*
        decoding when no `GOOGLE_CLIENT_ID` is configured (dev-only)."""

        if not settings.google_client_id:
            if settings.app_env != "dev":
                raise Unauthorized("Google sign-in is not configured")
            logger.warning(
                "No GOOGLE_CLIENT_ID configured – decoding ID token without verification (dev only)"
            )
        else:
            try:
                from google.auth.transport import requests as g_requests  # type: ignore
                from google.oauth2 import id_token as g_id_token  # type: ignore

                return await asyncio.to_thread(
                    g_id_token.verify_oauth2_token,
                    id_token_str,
                    g_requests.Request(),
                    settings.google_client_id,
                )
            except Exception as exc:
                raise Unauthorized(f"Invalid Google token: {exc}") from exc

        if settings.google_client_id:
            raise Unauthorized("Invalid Google token")

        # Dev-only fallback when GOOGLE_CLIENT_ID is unset.
        try:
            import jwt as pyjwt

            return pyjwt.decode(id_token_str, options={"verify_signature": False})
        except Exception as exc:
            raise Unauthorized(f"Invalid Google token: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Email + password
    # ------------------------------------------------------------------ #

    async def sign_up(
        self,
        *,
        email: str,
        password: str,
        confirm_password: str,
        user_agent: Optional[str],
        ip: Optional[str],
    ) -> Dict[str, Any]:
        if password != confirm_password:
            raise Conflict("Passwords do not match")
        if len(password) < 8:
            raise Conflict("Password must be at least 8 characters")

        existing = await self.users.by_email(email)
        if existing and existing.password_hash:
            raise Conflict("Email already registered")

        user = existing or await self.users.create(
            email=email,
            name=email.split("@")[0],
            password_hash=security.hash_password(password),
        )
        if existing and not existing.password_hash:
            await self.users.update_profile(
                existing.id, password_hash=security.hash_password(password)
            )
        await self.users.touch_login(user.id)
        return await self._build_response(user.id, user_agent=user_agent, ip=ip)

    async def sign_in_with_password(
        self,
        *,
        email: str,
        password: str,
        user_agent: Optional[str],
        ip: Optional[str],
    ) -> Dict[str, Any]:
        user = await self.users.by_email(email)
        if not user or not security.verify_password(password, user.password_hash or ""):
            raise Unauthorized("Invalid email or password")
        await self.users.touch_login(user.id)
        return await self._build_response(user.id, user_agent=user_agent, ip=ip)

    # ------------------------------------------------------------------ #
    # Refresh
    # ------------------------------------------------------------------ #

    async def refresh(
        self,
        *,
        refresh_token: str,
        user_agent: Optional[str],
        ip: Optional[str],
    ) -> Dict[str, Any]:
        token_hash = security.hash_refresh_token(refresh_token)
        rt = await self.refreshes.find_active(token_hash)
        if not rt:
            raise Unauthorized("Invalid or expired refresh token")
        new_token, new_id = await self._issue_refresh(
            rt.user_id, user_agent=user_agent, ip=ip
        )
        await self.refreshes.revoke(rt.id, rotated_to=new_id)
        access_token = await self._issue_access(rt.user_id)
        user = await self.users.get(rt.user_id)
        sub = await self.subs.active_for_user(rt.user_id)
        return self._payload(
            user,
            access_token,
            new_token,
            plan_code=sub.plan_code if sub else "free",
            subscription_end_date=(
                sub.current_period_end.isoformat()
                if sub and sub.current_period_end
                else None
            ),
        )

    async def logout(self, *, refresh_token: Optional[str]) -> None:
        if not refresh_token:
            return
        rt = await self.refreshes.find_active(
            security.hash_refresh_token(refresh_token)
        )
        if rt:
            await self.refreshes.revoke(rt.id)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _issue_access(self, user_id: uuid.UUID) -> str:
        sub = await self.subs.active_for_user(user_id)
        plan = sub.plan_code if sub else "free"
        user = await self.users.get(user_id)
        return security.issue_access_token(
            str(user_id),
            extra={
                "email": user.email if user else "",
                "plan": plan,
                "is_admin": bool(user.is_admin) if user else False,
            },
        )

    async def _issue_refresh(
        self, user_id: uuid.UUID, *, user_agent: Optional[str], ip: Optional[str]
    ) -> Tuple[str, uuid.UUID]:
        token = security.new_refresh_token()
        rt = await self.refreshes.store(
            user_id=user_id,
            token_hash=security.hash_refresh_token(token),
            expires_at=security.refresh_token_expiry(),
            user_agent=user_agent,
            ip_address=ip,
        )
        return token, rt.id

    async def _build_response(
        self, user_id: uuid.UUID, *, user_agent: Optional[str], ip: Optional[str]
    ) -> Dict[str, Any]:
        await self._clear_vocab_mission_cache(user_id)
        access = await self._issue_access(user_id)
        refresh, _ = await self._issue_refresh(user_id, user_agent=user_agent, ip=ip)
        user = await self.users.get(user_id)
        sub = await self.subs.active_for_user(user_id)
        return self._payload(
            user,
            access,
            refresh,
            plan_code=sub.plan_code if sub else "free",
            subscription_end_date=(
                sub.current_period_end.isoformat()
                if sub and sub.current_period_end
                else None
            ),
        )

    async def _clear_vocab_mission_cache(self, user_id: uuid.UUID) -> None:
        mission_date = datetime.now(VN_TZ).date()
        try:
            repo = LearningPersonalizationRepo(self.s)
            deleted = await repo.delete_daily_missions(
                user_id=user_id,
                mission_date=mission_date,
            )
            if deleted:
                logger.info(
                    "Cleared vocab daily mission cache on login user={} date={} rows={}",
                    user_id,
                    mission_date,
                    deleted,
                )
        except Exception as exc:
            logger.warning(
                "Could not clear vocab mission cache on login user={} err={}",
                user_id,
                exc,
            )

    def _payload(
        self,
        user,
        access: str,
        refresh: str,
        *,
        plan_code: str,
        subscription_end_date: Optional[str],
    ) -> Dict[str, Any]:
        if user is None:
            raise Unauthorized("User not found")
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": settings.jwt_access_ttl_minutes * 60,
            "user": _serialize_user(
                user,
                plan_code=plan_code,
                subscription_end_date=subscription_end_date,
            ),
        }


def _serialize_user(
    user,
    *,
    plan_code: str = "free",
    subscription_end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Shape that matches `useGoogleLogin.tsx` localStorage assumptions."""
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.email.split("@")[0],
        "full_name": user.name,
        "name": user.name,
        "picture": user.avatar_url or "/images/avatar-default.png",
        "google_id": None,
        "current_plan": plan_code,
        "subscription_end_date": subscription_end_date,
        "emailVerified": user.email_verified,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
