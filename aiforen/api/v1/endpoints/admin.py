"""Admin console metrics — matches primevocab-fe adminService.ts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import CurrentUser, get_current_admin, get_pg
from aiforen.services.admin_service import AdminService

router = APIRouter()


def _wrap(data: object) -> dict:
    return {"success": True, "data": data}


@router.get("/me")
async def admin_me(
    user: CurrentUser = Depends(get_current_admin),
    pg: AsyncSession = Depends(get_pg),
):
    svc = AdminService(pg)
    return _wrap(await svc.verify_admin(user.id))


@router.get("/metrics/overview")
async def metrics_overview(
    user: CurrentUser = Depends(get_current_admin),
    pg: AsyncSession = Depends(get_pg),
):
    _ = user
    svc = AdminService(pg)
    return _wrap(await svc.overview())


@router.get("/metrics/funnel")
async def metrics_funnel(
    days: int = Query(30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_admin),
    pg: AsyncSession = Depends(get_pg),
):
    _ = user
    svc = AdminService(pg)
    return _wrap(await svc.funnel(window_days=days))


@router.get("/metrics/retention")
async def metrics_retention(
    cohort_days: int = Query(14, ge=1, le=90),
    user: CurrentUser = Depends(get_current_admin),
    pg: AsyncSession = Depends(get_pg),
):
    _ = user
    svc = AdminService(pg)
    return _wrap(await svc.retention(cohort_days=cohort_days))
