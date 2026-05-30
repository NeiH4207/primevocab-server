"""Leaderboard from completed writing submissions in Postgres."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import get_pg
from aiforen.domain.sql_models import WritingSubmission

router = APIRouter()


@router.get("")
async def leaderboard(
    period: str = Query("weekly", pattern="^(daily|weekly|monthly|all)$"),
    pg: AsyncSession = Depends(get_pg),
):
    delta = {
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "monthly": timedelta(days=30),
        "all": timedelta(days=3650),
    }[period]
    since = datetime.utcnow() - delta

    score_expr = cast(
        WritingSubmission.assessment["scores"]["overall_score"].astext,
        Float,
    )
    stmt = (
        select(
            WritingSubmission.user_id,
            func.avg(score_expr).label("score"),
            func.count().label("tasks"),
        )
        .where(
            WritingSubmission.status == "completed",
            WritingSubmission.finished_at >= since,
            WritingSubmission.assessment.isnot(None),
        )
        .group_by(WritingSubmission.user_id)
        .order_by(func.avg(score_expr).desc())
        .limit(20)
    )
    rows = (await pg.execute(stmt)).all()
    leaderboard_rows = [
        {
            "user_id": str(row.user_id),
            "score": round(float(row.score or 0), 2),
            "tasks": int(row.tasks or 0),
        }
        for row in rows
    ]
    return {
        "status": "success",
        "data": {"period": period, "leaderboard": leaderboard_rows},
    }
