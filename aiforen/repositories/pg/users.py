"""User + auth-provider data access."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import AuthProvider, User


class UserRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, user_id: uuid.UUID | str) -> Optional[User]:
        if isinstance(user_id, str):
            try:
                user_id = uuid.UUID(user_id)
            except ValueError:
                return None
        return await self.s.get(User, user_id)

    async def by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email.lower())
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        name: str,
        password_hash: Optional[str] = None,
        avatar_url: Optional[str] = None,
        locale: str = "en",
        email_verified: bool = False,
        is_admin: bool = False,
    ) -> User:
        user = User(
            email=email.lower(),
            name=name,
            password_hash=password_hash,
            avatar_url=avatar_url,
            locale=locale,
            email_verified=email_verified,
            is_admin=is_admin,
        )
        self.s.add(user)
        await self.s.flush()
        return user

    async def touch_login(self, user_id: uuid.UUID) -> None:
        await self.s.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=datetime.now(timezone.utc))
        )

    async def update_profile(self, user_id: uuid.UUID, **fields: Any) -> Optional[User]:
        if not fields:
            return await self.get(user_id)
        await self.s.execute(update(User).where(User.id == user_id).values(**fields))
        return await self.get(user_id)


class AuthProviderRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def list_providers_for_user(self, user_id: uuid.UUID) -> list[str]:
        stmt = (
            select(AuthProvider.provider)
            .where(AuthProvider.user_id == user_id)
            .distinct()
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return sorted({str(p) for p in rows if p})

    async def find(self, provider: str, provider_uid: str) -> Optional[AuthProvider]:
        stmt = select(AuthProvider).where(
            AuthProvider.provider == provider,
            AuthProvider.provider_uid == provider_uid,
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def link(
        self,
        user_id: uuid.UUID,
        provider: str,
        provider_uid: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuthProvider:
        ap = AuthProvider(
            user_id=user_id,
            provider=provider,
            provider_uid=provider_uid,
            payload=payload or {},
        )
        self.s.add(ap)
        await self.s.flush()
        return ap
