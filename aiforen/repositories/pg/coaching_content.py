"""Postgres persistence for level-based coaching reading catalog."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiforen.domain.sql_models import CoachingReadingUnit, CoachingReadingUnitQuestion


class CoachingContentRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get_published_unit(
        self, cefr_level: str, day_number: int
    ) -> Optional[CoachingReadingUnit]:
        level = (cefr_level or "").upper()
        return (
            await self.s.execute(
                select(CoachingReadingUnit)
                .options(selectinload(CoachingReadingUnit.questions))
                .where(
                    CoachingReadingUnit.cefr_level == level,
                    CoachingReadingUnit.day_number == day_number,
                    CoachingReadingUnit.status == "published",
                )
            )
        ).scalar_one_or_none()

    async def upsert_unit(
        self,
        *,
        unit_id: str,
        cefr_level: str,
        day_number: int,
        topic_slug: str,
        topic_title: str,
        title: str,
        paragraphs: Sequence[str],
        source_label: str,
        estimated_minutes: int = 8,
        question_limit: int = 7,
        content_version: int = 1,
        status: str = "published",
        questions: Sequence[Dict[str, Any]],
        vocab_keywords: Sequence[Dict[str, Any]] | None = None,
    ) -> CoachingReadingUnit:
        level = cefr_level.upper()
        unit_values = {
            "id": unit_id,
            "cefr_level": level,
            "day_number": day_number,
            "topic_slug": topic_slug,
            "topic_title": topic_title,
            "title": title,
            "paragraphs": list(paragraphs),
            "estimated_minutes": estimated_minutes,
            "source_label": source_label,
            "question_limit": question_limit,
            "content_version": content_version,
            "vocab_keywords": list(vocab_keywords or []),
            "status": status,
        }
        stmt = pg_insert(CoachingReadingUnit).values(**unit_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CoachingReadingUnit.id],
            set_={
                "cefr_level": stmt.excluded.cefr_level,
                "day_number": stmt.excluded.day_number,
                "topic_slug": stmt.excluded.topic_slug,
                "topic_title": stmt.excluded.topic_title,
                "title": stmt.excluded.title,
                "paragraphs": stmt.excluded.paragraphs,
                "estimated_minutes": stmt.excluded.estimated_minutes,
                "source_label": stmt.excluded.source_label,
                "question_limit": stmt.excluded.question_limit,
                "content_version": stmt.excluded.content_version,
                "vocab_keywords": stmt.excluded.vocab_keywords,
                "status": stmt.excluded.status,
            },
        )
        await self.s.execute(stmt)

        await self.s.execute(
            delete(CoachingReadingUnitQuestion).where(
                CoachingReadingUnitQuestion.unit_id == unit_id
            )
        )
        for row in questions:
            self.s.add(
                CoachingReadingUnitQuestion(
                    id=str(row["id"]),
                    unit_id=unit_id,
                    sort_order=int(row["sort_order"]),
                    question_type=str(row["question_type"]),
                    prompt=str(row["prompt"]),
                    options=list(row.get("options") or []) or None,
                    correct_option=str(row["correct_option"]),
                    acceptable_answers=list(row.get("acceptable_answers") or [])
                    or None,
                    explanation=row.get("explanation"),
                    source_word=row.get("source_word"),
                )
            )
        await self.s.flush()
        unit = await self.get_published_unit(level, day_number)
        if unit is None:
            raise RuntimeError(f"Failed to upsert coaching reading unit {unit_id}")
        return unit
