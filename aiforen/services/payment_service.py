"""Payment + plan upgrade flow.

Confirm / status checks call the configured provider's ``verify_status`` first;
subscription is granted only when the provider reports ``succeeded``.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.config import get_settings
from aiforen.core.errors import Forbidden, NotFound
from aiforen.integrations.payment import PaymentIntent, get_payment_provider
from aiforen.integrations.payment.mock import MockPaymentProvider
from aiforen.repositories.pg.payments import PaymentRepo
from aiforen.repositories.pg.plans import PlanRepo, SubscriptionRepo

_PLAN_PRICE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "standard": {
        "name": "Basic",
        "description": "Daily IELTS practice with the standard AI model.",
        "price_usd": 1.89,
        "quarterly_discount": 10.0,
        "half_yearly_discount": 10.0,
        "features": {"display_name": "Basic", "model": "standard"},
    },
    "premium": {
        "name": "Pro",
        "description": "Same focused workflow, powered by a stronger AI model.",
        "price_usd": 2.89,
        "quarterly_discount": 15.0,
        "half_yearly_discount": 15.0,
        "features": {"display_name": "Pro", "model": "advanced"},
    },
}

settings = get_settings()


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.s = session
        self.plans = PlanRepo(session)
        self.subs = SubscriptionRepo(session)
        self.payments = PaymentRepo(session)
        self.provider = get_payment_provider()

    async def create_intent(
        self,
        *,
        user_id: str,
        plan_code: str,
        months: int,
    ) -> Dict[str, Any]:
        plan = await self.plans.get(plan_code)
        if not plan:
            raise NotFound(f"Plan {plan_code} not found")
        override = _PLAN_PRICE_OVERRIDES.get(plan_code, {})
        # Apply discount for longer cycles
        base = float(override.get("price_usd", plan.price_usd)) * months
        quarterly_discount = float(
            override.get("quarterly_discount", plan.quarterly_discount or 0)
        )
        half_yearly_discount = float(
            override.get("half_yearly_discount", plan.half_yearly_discount or 0)
        )
        if months >= 6:
            base *= 1 - half_yearly_discount / 100
        elif months >= 3:
            base *= 1 - quarterly_discount / 100

        amount_minor = max(0, int(round(base * 100)))
        intent: PaymentIntent = await self.provider.create_intent(
            user_id=user_id,
            plan_code=plan_code,
            months=months,
            amount_minor=amount_minor,
            currency=plan.currency,
            return_url=f"{settings.frontend_base_url}/profile",
        )

        await self.payments.create(
            user_id=uuid.UUID(user_id),
            provider=intent.provider,
            provider_payment_id=intent.provider_payment_id,
            amount_minor=intent.amount_minor,
            currency=intent.currency,
            plan_code=plan_code,
            months=months,
            metadata={"return_url": f"{settings.frontend_base_url}/profile"},
        )

        return {
            "provider": intent.provider,
            "payment_id": intent.provider_payment_id,
            "amount_minor": intent.amount_minor,
            "amount": amount_minor / 100,
            "currency": intent.currency,
            "pay_url": intent.pay_url,
            "qr_image": intent.extra.get("qr_image"),
            "expires_at": intent.expires_at,
            "plan_code": plan_code,
            "months": months,
        }

    async def check_status(
        self, *, provider_payment_id: str, user_id: str
    ) -> Dict[str, Any]:
        """Poll provider + DB; grant subscription only when provider reports paid."""
        payment = await self.payments.get_by_provider_id(
            self.provider.name, provider_payment_id
        )
        if not payment:
            raise NotFound("Payment not found")
        if str(payment.user_id) != user_id:
            raise Forbidden("Payment does not belong to this user")

        if payment.status == "succeeded":
            sub = await self.subs.active_for_user(payment.user_id)
            return {
                "status": "succeeded",
                "payment_id": str(payment.id),
                "plan_code": payment.plan_code,
                "current_period_end": (
                    sub.current_period_end.isoformat()
                    if sub and sub.current_period_end
                    else None
                ),
            }

        provider_status = await self.provider.verify_status(provider_payment_id)
        if provider_status != "succeeded":
            return {
                "status": "pending",
                "provider_status": provider_status,
                "payment_id": str(payment.id),
            }

        return await self._finalize_successful_payment(payment)

    async def confirm(
        self, *, provider_payment_id: str, user_id: str, provider: str | None = None
    ) -> Dict[str, Any]:
        """Idempotent finalize after provider reports payment succeeded."""
        _ = provider  # legacy query param; provider taken from payment row
        return await self.check_status(
            provider_payment_id=provider_payment_id, user_id=user_id
        )

    async def _finalize_successful_payment(self, payment) -> Dict[str, Any]:
        if payment.status == "succeeded":
            sub = await self.subs.active_for_user(payment.user_id)
            return {
                "status": "succeeded",
                "payment_id": str(payment.id),
                "plan_code": payment.plan_code,
                "current_period_end": (
                    sub.current_period_end.isoformat()
                    if sub and sub.current_period_end
                    else None
                ),
            }

        sub = await self.subs.grant(
            user_id=payment.user_id,
            plan_code=payment.plan_code or "standard",
            months=payment.months,
            price_paid=payment.amount_minor / 100,
            currency=payment.currency,
            payment_method=self.provider.name,
        )
        await self.payments.mark_succeeded(payment.id, sub.id)
        return {
            "status": "succeeded",
            "payment_id": str(payment.id),
            "subscription_id": str(sub.id),
            "plan_code": sub.plan_code,
            "current_period_end": sub.current_period_end.isoformat(),
        }

    async def mark_mock_paid(
        self, *, provider_payment_id: str, user_id: str
    ) -> Dict[str, Any]:
        """Dev-only: simulate provider webhook after user completes mock checkout."""
        if self.provider.name != "mock":
            raise Forbidden(
                "Mock completion is only available with the mock payment provider"
            )
        payment = await self.payments.get_by_provider_id("mock", provider_payment_id)
        if not payment:
            raise NotFound("Payment not found")
        if str(payment.user_id) != user_id:
            raise Forbidden("Payment does not belong to this user")
        if isinstance(self.provider, MockPaymentProvider):
            if not MockPaymentProvider.mark_paid(provider_payment_id):
                raise NotFound("Payment not found")
        return await self.check_status(
            provider_payment_id=provider_payment_id, user_id=user_id
        )

    async def list_plans(self) -> Dict[str, Any]:
        plans = await self.plans.list_active()
        return {
            "plans": [
                {
                    "code": p.code,
                    "name": _PLAN_PRICE_OVERRIDES.get(p.code, {}).get("name", p.name),
                    "description": _PLAN_PRICE_OVERRIDES.get(p.code, {}).get(
                        "description", p.description
                    ),
                    "price_usd": float(
                        _PLAN_PRICE_OVERRIDES.get(p.code, {}).get(
                            "price_usd", p.price_usd
                        )
                    ),
                    "currency": p.currency,
                    "monthly_assessments": p.monthly_assessments,
                    "daily_ai_feedback": p.daily_ai_feedback,
                    "daily_vocab_reviews": p.daily_vocab_reviews,
                    "can_create_personal_tasks": p.can_create_personal_tasks,
                    "quarterly_discount": float(
                        _PLAN_PRICE_OVERRIDES.get(p.code, {}).get(
                            "quarterly_discount", p.quarterly_discount or 0
                        )
                    ),
                    "half_yearly_discount": float(
                        _PLAN_PRICE_OVERRIDES.get(p.code, {}).get(
                            "half_yearly_discount", p.half_yearly_discount or 0
                        )
                    ),
                    "features": {
                        **(p.features or {}),
                        **_PLAN_PRICE_OVERRIDES.get(p.code, {}).get("features", {}),
                    },
                    "sort_order": p.sort_order,
                }
                for p in plans
            ]
        }
