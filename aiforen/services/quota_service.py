"""Quota guard.

Reads the user's active plan from Postgres + the current period counter
and either enforces a limit or increments and returns.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.config import get_settings
from aiforen.core.errors import QuotaExceeded
from aiforen.domain.enums import QuotaKind
from aiforen.repositories.pg.plans import PlanRepo, SubscriptionRepo
from aiforen.repositories.pg.usage import UsageRepo

settings = get_settings()


def _month_start(today: date | None = None) -> date:
    today = today or date.today()
    return today.replace(day=1)


def _today() -> date:
    return date.today()


class QuotaService:
    def __init__(self, session: AsyncSession):
        self.subs = SubscriptionRepo(session)
        self.plans = PlanRepo(session)
        self.usage = UsageRepo(session)

    async def _plan_limit(
        self, user_id: uuid.UUID, kind: QuotaKind
    ) -> Tuple[int, date]:
        sub = await self.subs.active_for_user(user_id)
        plan_code = sub.plan_code if sub else "free"
        plan = await self.plans.get(plan_code)

        if kind == QuotaKind.assessment:
            limit = (
                plan.monthly_assessments
                if plan
                else settings.free_assessments_per_month
            )
            period = _month_start()
        elif kind == QuotaKind.ai_feedback:
            limit = plan.daily_ai_feedback if plan else 3
            period = _today()
        elif kind == QuotaKind.vocab_ai_eval:
            limit = settings.free_vocab_ai_eval_total
            period = date(2000, 1, 1)
        elif kind == QuotaKind.vocab_word:
            limit = plan.daily_vocab_reviews if plan else 20
            period = _today()
        else:
            limit = 9999
            period = _today()
        return limit, period

    async def consume(self, user_id_str: str, kind: QuotaKind) -> Tuple[int, int]:
        user_id = uuid.UUID(user_id_str)
        limit, period = await self._plan_limit(user_id, kind)
        # Local development should not block end-to-end manual testing flows.
        enforce_limit = not (settings.app_env == "dev" and kind == QuotaKind.assessment)
        # Increment atomically first, then verify. This closes the
        # read-then-write race where two concurrent requests both read a
        # below-limit counter and both proceed past the limit.
        new_count = await self.usage.increment(user_id, kind, period=period)
        if enforce_limit and limit > 0 and new_count > limit:
            # Roll back the speculative increment so the counter stays accurate.
            await self.usage.increment(user_id, kind, period=period, by=-1)
            raise QuotaExceeded(
                f"You've reached the {kind.value} limit for this period ({limit}). Upgrade to continue."
            )
        return new_count, limit

    async def snapshot(self, user_id_str: str, kind: QuotaKind) -> Tuple[int, int]:
        user_id = uuid.UUID(user_id_str)
        limit, period = await self._plan_limit(user_id, kind)
        current = await self.usage.current(user_id, kind, period)
        return current, limit
