"""Grammar content repository on Postgres."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import GrammarStructure


def _to_dict(row: GrammarStructure) -> Dict[str, Any]:
    return {
        "structure_id": row.structure_id,
        "name": row.name,
        "structure_pattern": row.structure_pattern,
        "description": row.description,
        "category": row.category,
        "task_type": row.task_type,
        "band_score": float(row.band_score or 0),
        "difficulty_level": row.difficulty_level,
        "examples": list(row.examples or []),
        "common_errors": list(row.common_errors or []),
        "tags": list(row.tags or []),
        "total_attempts": row.total_attempts,
        "success_rate": float(row.success_rate or 0),
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class GrammarRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def list(
        self,
        *,
        category: Optional[str] = None,
        difficulty: Optional[str] = None,
        task_type: Optional[str] = None,
        band_min: Optional[float] = None,
        band_max: Optional[float] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        q = select(GrammarStructure).where(GrammarStructure.is_active.is_(True))
        if category:
            q = q.where(GrammarStructure.category == category)
        if difficulty:
            q = q.where(GrammarStructure.difficulty_level == difficulty)
        if task_type:
            q = q.where(GrammarStructure.task_type.in_([task_type, "Both"]))
        if band_min is not None:
            q = q.where(GrammarStructure.band_score >= band_min)
        if band_max is not None:
            q = q.where(GrammarStructure.band_score <= band_max)
        rows = (await self.s.execute(q.offset(skip).limit(limit))).scalars()
        return [_to_dict(r) for r in rows]

    async def get(self, structure_id: str) -> Optional[Dict[str, Any]]:
        row = await self.s.get(GrammarStructure, structure_id)
        return _to_dict(row) if row else None

    async def categories(self) -> List[str]:
        rows = (
            await self.s.execute(
                select(GrammarStructure.category)
                .where(GrammarStructure.is_active.is_(True))
                .distinct()
            )
        ).all()
        return [r[0] for r in rows if r[0]]

    async def insert_many(self, structures: List[Dict[str, Any]]) -> None:
        for st in structures:
            stmt = pg_insert(GrammarStructure).values(
                structure_id=st["structure_id"],
                name=st["name"],
                structure_pattern=st["structure_pattern"],
                description=st.get("description") or "",
                category=st["category"],
                task_type=st.get("task_type") or "Both",
                band_score=float(st.get("band_score") or 6.0),
                difficulty_level=st.get("difficulty_level") or "intermediate",
                examples=list(st.get("examples") or []),
                common_errors=list(st.get("common_errors") or []),
                tags=list(st.get("tags") or []),
                total_attempts=int(st.get("total_attempts") or 0),
                success_rate=float(st.get("success_rate") or 0),
                is_active=bool(st.get("is_active", True)),
                created_at=st.get("created_at") or datetime.utcnow(),
                updated_at=st.get("updated_at") or datetime.utcnow(),
            )
            await self.s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[GrammarStructure.structure_id],
                    set_={
                        "name": st["name"],
                        "structure_pattern": st["structure_pattern"],
                        "description": st.get("description") or "",
                        "category": st["category"],
                        "task_type": st.get("task_type") or "Both",
                        "band_score": float(st.get("band_score") or 6.0),
                        "difficulty_level": st.get("difficulty_level")
                        or "intermediate",
                        "examples": list(st.get("examples") or []),
                        "common_errors": list(st.get("common_errors") or []),
                        "tags": list(st.get("tags") or []),
                        "is_active": bool(st.get("is_active", True)),
                        "updated_at": datetime.utcnow(),
                    },
                )
            )
