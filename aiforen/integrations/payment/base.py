"""Abstraction for payment providers (PayOS / Stripe / mock)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol


@dataclass
class PaymentIntent:
    provider: str
    provider_payment_id: str
    pay_url: str  # for QR/redirect
    amount_minor: int
    currency: str
    expires_at: int  # unix seconds
    extra: Dict[str, Any]


class PaymentProvider(Protocol):
    name: str

    async def create_intent(
        self,
        *,
        user_id: str,
        plan_code: str,
        months: int,
        amount_minor: int,
        currency: str,
        return_url: str,
    ) -> PaymentIntent: ...

    async def verify_status(self, provider_payment_id: str) -> str: ...
