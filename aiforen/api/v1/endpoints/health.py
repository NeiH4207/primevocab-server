from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core import db as core_db
from aiforen.core.deps import get_pg

router = APIRouter()


@router.get("/health", tags=["Status"])
async def health_check(pg: AsyncSession = Depends(get_pg)):
    pg_ok = redis_ok = False
    try:
        await pg.execute(text("SELECT 1"))
        pg_ok = True
    except Exception:
        pass
    try:
        await core_db.redis_client().ping()
        redis_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if (pg_ok and redis_ok) else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": {"postgres": pg_ok, "redis": redis_ok},
    }
