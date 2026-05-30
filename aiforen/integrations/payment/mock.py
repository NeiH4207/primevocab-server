"""Mock payment provider for local development.

Payments stay ``pending`` until explicitly marked paid (dev webhook or
``mark_paid``). Polling never auto-succeeds — mirrors real provider behavior.
"""

from __future__ import annotations

import secrets
import time
from typing import Any, ClassVar, Dict
from urllib.parse import urlparse

from .base import PaymentIntent, PaymentProvider

# Match product expectation: short checkout window (seconds).
PAYMENT_EXPIRY_SECONDS = 60


def _frontend_payment_url(return_url: str, ref: str) -> str:
    parsed = urlparse(return_url)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
    else:
        origin = "http://localhost:3000"
    return f"{origin}/payment?mock_ref={ref}"


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    # In-process ledger for dev; reset on API restart.
    _records: ClassVar[Dict[str, Dict[str, Any]]] = {}

    async def create_intent(
        self,
        *,
        user_id: str,
        plan_code: str,
        months: int,
        amount_minor: int,
        currency: str,
        return_url: str,
    ) -> PaymentIntent:
        ref = secrets.token_urlsafe(12)
        self._records[ref] = {
            "status": "pending",
            "created_at": time.time(),
            "user_id": user_id,
            "plan_code": plan_code,
            "months": months,
        }
        return PaymentIntent(
            provider=self.name,
            provider_payment_id=ref,
            pay_url=_frontend_payment_url(return_url, ref),
            amount_minor=amount_minor,
            currency=currency,
            expires_at=int(time.time()) + PAYMENT_EXPIRY_SECONDS,
            extra={
                "qr_image": (
                    "https://api.qrserver.com/v1/create-qr-code/"
                    f"?size=240x240&data=mock-pay-{ref}"
                ),
                "plan": plan_code,
                "months": months,
                "user_id": user_id,
            },
        )

    async def verify_status(self, provider_payment_id: str) -> str:
        rec = self._records.get(provider_payment_id)
        if not rec:
            return "not_found"
        return str(rec.get("status", "pending"))

    @classmethod
    def mark_paid(cls, provider_payment_id: str) -> bool:
        rec = cls._records.get(provider_payment_id)
        if not rec:
            return False
        rec["status"] = "succeeded"
        rec["paid_at"] = time.time()
        return True
