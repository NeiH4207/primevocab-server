"""Plans + subscriptions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import Plan, Subscription


class PlanRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, code: str) -> Optional[Plan]:
        return await self.s.get(Plan, code)

    async def list_active(self) -> List[Plan]:
        stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
        return list((await self.s.execute(stmt)).scalars())

    async def upsert(
        self,
        *,
        code: str,
        name: str,
        description: str,
        price_usd: float,
        monthly_assessments: int,
        daily_ai_feedback: int,
        daily_vocab_reviews: int,
        can_create_personal_tasks: bool,
        quarterly_discount: float = 0.0,
        half_yearly_discount: float = 0.0,
        features: Optional[dict] = None,
        sort_order: int = 0,
    ) -> Plan:
        existing = await self.get(code)
        if existing:
            existing.name = name
            existing.description = description
            existing.price_usd = price_usd
            existing.monthly_assessments = monthly_assessments
            existing.daily_ai_feedback = daily_ai_feedback
            existing.daily_vocab_reviews = daily_vocab_reviews
            existing.can_create_personal_tasks = can_create_personal_tasks
            existing.quarterly_discount = quarterly_discount
            existing.half_yearly_discount = half_yearly_discount
            existing.features = features or {}
            existing.sort_order = sort_order
            existing.is_active = True
            await self.s.flush()
            return existing
        plan = Plan(
            code=code,
            name=name,
            description=description,
            price_usd=price_usd,
            monthly_assessments=monthly_assessments,
            daily_ai_feedback=daily_ai_feedback,
            daily_vocab_reviews=daily_vocab_reviews,
            can_create_personal_tasks=can_create_personal_tasks,
            quarterly_discount=quarterly_discount,
            half_yearly_discount=half_yearly_discount,
            features=features or {},
            sort_order=sort_order,
            is_active=True,
        )
        self.s.add(plan)
        await self.s.flush()
        return plan


class SubscriptionRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def active_for_user(self, user_id: uuid.UUID) -> Optional[Subscription]:
        stmt = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > datetime.now(timezone.utc),
            )
            .order_by(Subscription.current_period_end.desc())
        )
        return (await self.s.execute(stmt)).scalars().first()

    async def grant(
        self,
        *,
        user_id: uuid.UUID,
        plan_code: str,
        billing_cycle: str = "monthly",
        months: int = 1,
        price_paid: float = 0.0,
        currency: str = "USD",
        payment_method: Optional[str] = None,
    ) -> Subscription:
        now = datetime.now(timezone.utc)
        sub = Subscription(
            user_id=user_id,
            plan_code=plan_code,
            billing_cycle=billing_cycle,
            status="active",
            price_paid=price_paid,
            currency=currency,
            current_period_start=now,
            current_period_end=now + timedelta(days=30 * months),
            auto_renewal=False,
            payment_method=payment_method,
        )
        self.s.add(sub)
        await self.s.flush()
        return sub
