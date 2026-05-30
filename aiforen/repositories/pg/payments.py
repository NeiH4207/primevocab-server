"""Payments + public_assessments mapping."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import Payment, PublicAssessment


class PaymentRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        plan_code: str,
        months: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Payment:
        payment = Payment(
            user_id=user_id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            amount_minor=amount_minor,
            currency=currency,
            plan_code=plan_code,
            months=months,
            status="pending",
            metadata_=metadata or {},
        )
        self.s.add(payment)
        await self.s.flush()
        return payment

    async def get_by_provider_id(
        self, provider: str, provider_payment_id: str
    ) -> Optional[Payment]:
        stmt = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_payment_id == provider_payment_id,
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def mark_succeeded(
        self, payment_id: uuid.UUID, subscription_id: uuid.UUID
    ) -> None:
        payment = await self.s.get(Payment, payment_id)
        if payment:
            payment.status = "succeeded"
            payment.subscription_id = subscription_id


class PublicAssessmentRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, public_id: str) -> Optional[PublicAssessment]:
        return await self.s.get(PublicAssessment, public_id)

    async def by_submission(self, submission_id: str) -> Optional[PublicAssessment]:
        stmt = select(PublicAssessment).where(
            PublicAssessment.submission_id == submission_id
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        public_id: str,
        submission_id: str,
        user_id: uuid.UUID,
    ) -> PublicAssessment:
        pa = PublicAssessment(
            public_id=public_id,
            submission_id=submission_id,
            user_id=user_id,
        )
        self.s.add(pa)
        await self.s.flush()
        return pa
