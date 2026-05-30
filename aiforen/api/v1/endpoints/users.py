"""User profile endpoints (mounted under /auth/me to match the FE)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import CurrentUser, get_current_user, get_pg
from aiforen.core.errors import AppError, NotFound
from aiforen.services.learning_service import LearningService
from aiforen.services.user_service import UserService

router = APIRouter()


class UpdateMeIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    language_preference: Optional[str] = Field(default=None, max_length=8)
    timezone: Optional[str] = Field(default=None, max_length=64)


class ResetLearningIn(BaseModel):
    confirm: str = Field(..., min_length=1, max_length=32)


@router.get("/me")
async def me(
    user: CurrentUser = Depends(get_current_user), pg: AsyncSession = Depends(get_pg)
):
    svc = UserService(pg)
    profile = await svc.me(user.id)
    if not profile:
        raise NotFound("User not found")
    return {"status": "success", "data": profile}


@router.patch("/me")
async def update_me(
    payload: UpdateMeIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = UserService(pg)
    profile = await svc.update_me(
        user.id,
        name=payload.name,
        locale=payload.language_preference,
        timezone=payload.timezone,
    )
    if not profile:
        raise NotFound("User not found")
    return {"status": "success", "data": profile}


@router.post("/me/reset-learning")
async def reset_my_learning(
    payload: ResetLearningIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    if payload.confirm.strip().upper() != "RESET":
        raise AppError(
            "Type RESET in confirm to clear learning history.",
            code="invalid_confirm",
            status_code=400,
        )
    svc = LearningService(pg)
    data = await svc.reset_user_learning_data(user_id=user.id)
    return {"status": "success", "data": data}
