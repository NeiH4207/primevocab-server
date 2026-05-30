"""Vocab attempt history on Postgres."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import VocabAttempt


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class VocabAttemptRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def delete_all_for_user(self, user_id: str) -> int:
        result = await self.s.execute(
            delete(VocabAttempt).where(VocabAttempt.user_id == _uuid(user_id))
        )
        return int(result.rowcount or 0)

    async def insert(self, attempt: Dict[str, Any]) -> Dict[str, Any]:
        row = VocabAttempt(
            attempt_id=attempt["attempt_id"],
            user_id=_uuid(attempt["user_id"]),
            word_id=attempt["word_id"],
            pack_id=attempt.get("pack_id"),
            attempt_type=attempt["attempt_type"],
            is_correct=attempt.get("is_correct"),
            answer=attempt.get("answer"),
            ai_feedback=attempt.get("ai_feedback"),
            created_at=attempt.get("created_at") or datetime.utcnow(),
        )
        self.s.add(row)
        await self.s.flush()
        return attempt

    async def list_recent(
        self, user_id: str, *, limit: int = 20
    ) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(VocabAttempt)
                .where(VocabAttempt.user_id == _uuid(user_id))
                .order_by(VocabAttempt.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [
            {
                "attempt_id": r.attempt_id,
                "user_id": str(r.user_id),
                "word_id": r.word_id,
                "pack_id": r.pack_id,
                "attempt_type": r.attempt_type,
                "is_correct": r.is_correct,
                "answer": r.answer,
                "ai_feedback": r.ai_feedback,
                "created_at": r.created_at,
            }
            for r in rows
        ]
