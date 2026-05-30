"""Refresh-token store with rotation support."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import RefreshToken


class RefreshTokenRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def store(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> RefreshToken:
        rt = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.s.add(rt)
        await self.s.flush()
        return rt

    async def find_active(self, token_hash: str) -> Optional[RefreshToken]:
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def revoke(
        self, token_id: uuid.UUID, *, rotated_to: Optional[uuid.UUID] = None
    ) -> None:
        await self.s.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(revoked_at=datetime.now(timezone.utc), rotated_to=rotated_to)
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        await self.s.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
