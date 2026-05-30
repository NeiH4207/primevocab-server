"""User profile + plan resolution."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import User
from aiforen.repositories.pg.plans import SubscriptionRepo
from aiforen.repositories.pg.users import AuthProviderRepo, UserRepo


class UserService:
    def __init__(self, session: AsyncSession):
        self.users = UserRepo(session)
        self.subs = SubscriptionRepo(session)
        self.auth_providers = AuthProviderRepo(session)

    @staticmethod
    def _iso(dt) -> Optional[str]:
        return dt.isoformat() if dt else None

    def _serialize_profile(
        self,
        user: User,
        *,
        plan_code: str,
        subscription_end_date: Optional[str],
        auth_providers: list[str],
    ) -> Dict[str, Any]:
        return {
            "id": str(user.id),
            "email": user.email,
            "username": user.email.split("@")[0],
            "full_name": user.name,
            "name": user.name,
            "picture": user.avatar_url or "/images/avatar-default.png",
            "avatar_url": user.avatar_url,
            "current_plan": plan_code,
            "subscription_end_date": subscription_end_date,
            "email_verified": user.email_verified,
            "emailVerified": user.email_verified,
            "is_active": user.is_active,
            "active": user.is_active,
            "is_admin": user.is_admin,
            "locale": user.locale,
            "language_preference": user.locale,
            "timezone": user.timezone,
            "has_password": bool(user.password_hash),
            "auth_providers": auth_providers,
            "created_at": self._iso(user.created_at),
            "updated_at": self._iso(user.updated_at),
            "last_login_at": self._iso(user.last_login_at),
        }

    async def update_me(
        self,
        user_id: str,
        *,
        name: Optional[str] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            return None
        fields: Dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if locale is not None:
            fields["locale"] = locale
        if timezone is not None:
            fields["timezone"] = timezone
        if fields:
            await self.users.update_profile(uid, **fields)
        return await self.me(user_id)

    async def me(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            return None
        user = await self.users.get(uid)
        if not user:
            return None
        sub = await self.subs.active_for_user(uid)
        providers = await self.auth_providers.list_providers_for_user(uid)
        return self._serialize_profile(
            user,
            plan_code=sub.plan_code if sub else "free",
            subscription_end_date=self._iso(sub.current_period_end) if sub else None,
            auth_providers=providers,
        )
