"""Payment endpoints (mock provider)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.config import get_settings
from aiforen.core.deps import CurrentUser, get_current_user, get_pg
from aiforen.core.errors import Forbidden
from aiforen.services.payment_service import PaymentService

settings = get_settings()

router = APIRouter()


class CreateIntentIn(BaseModel):
    plan_code: str
    months: int = 1


class WebhookIn(BaseModel):
    provider_payment_id: str


@router.get("/plans")
async def list_plans(pg: AsyncSession = Depends(get_pg)):
    svc = PaymentService(pg)
    return {"status": "success", "data": await svc.list_plans()}


@router.post("/intent")
async def create_intent(
    payload: CreateIntentIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = PaymentService(pg)
    intent = await svc.create_intent(
        user_id=user.id, plan_code=payload.plan_code, months=payload.months
    )
    return {"status": "success", "data": intent}


@router.get("/status")
async def payment_status(
    provider_payment_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    """Poll payment state without granting until the provider reports paid."""
    svc = PaymentService(pg)
    return {
        "status": "success",
        "data": await svc.check_status(
            provider_payment_id=provider_payment_id,
            user_id=user.id,
        ),
    }


@router.post("/confirm")
async def confirm_payment(
    payload: WebhookIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    """Finalize subscription after provider reports payment succeeded (idempotent)."""
    svc = PaymentService(pg)
    return {
        "status": "success",
        "data": await svc.confirm(
            provider_payment_id=payload.provider_payment_id,
            user_id=user.id,
        ),
    }


@router.post("/webhook/mock")
async def mock_webhook(
    payload: WebhookIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    if settings.app_env != "dev":
        raise Forbidden("Mock webhook is only available in development")
    svc = PaymentService(pg)
    return {
        "status": "success",
        "data": await svc.mark_mock_paid(
            provider_payment_id=payload.provider_payment_id,
            user_id=user.id,
        ),
    }
