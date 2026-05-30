"""Quota counters in Postgres.  Atomic INSERT … ON CONFLICT to increment."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.enums import QuotaKind
from aiforen.domain.sql_models import UsageQuota


class UsageRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def increment(
        self, user_id: uuid.UUID, kind: QuotaKind, *, period: date, by: int = 1
    ) -> int:
        stmt = (
            pg_insert(UsageQuota)
            .values(user_id=user_id, kind=kind.value, period_start=period, count=by)
            .on_conflict_do_update(
                constraint="uq_usage_user_kind_period",
                set_={"count": UsageQuota.__table__.c.count + by},
            )
            .returning(UsageQuota.count)
        )
        result = await self.s.execute(stmt)
        return int(result.scalar_one())

    async def current(self, user_id: uuid.UUID, kind: QuotaKind, period: date) -> int:
        stmt = select(UsageQuota.count).where(
            UsageQuota.user_id == user_id,
            UsageQuota.kind == kind.value,
            UsageQuota.period_start == period,
        )
        return int((await self.s.execute(stmt)).scalar_one_or_none() or 0)
