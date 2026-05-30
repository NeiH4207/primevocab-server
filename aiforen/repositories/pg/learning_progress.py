"""Learner progress on Postgres (vocab + grammar)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import GrammarLearningProgress, VocabUserWordState
from aiforen.repositories.pg.progress_adapters import (
    grammar_progress_to_dict,
    progress_to_word_state_values,
    word_state_to_progress,
)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class LearningProgressRepo:
    """Spaced-repetition state (SM-2 inspired)."""

    def __init__(self, session: AsyncSession):
        self.s = session

    async def delete_all_for_user(self, user_id: str) -> int:
        uid = _uuid(user_id)
        r1 = await self.s.execute(
            delete(VocabUserWordState).where(VocabUserWordState.user_id == uid)
        )
        r2 = await self.s.execute(
            delete(GrammarLearningProgress).where(
                GrammarLearningProgress.user_id == uid
            )
        )
        return int((r1.rowcount or 0) + (r2.rowcount or 0))

    async def list_for_user(
        self,
        user_id: str,
        *,
        content_type: Optional[str] = None,
        mastery_level: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        uid = _uuid(user_id)
        if content_type in (None, "vocabulary"):
            q = select(VocabUserWordState).where(VocabUserWordState.user_id == uid)
            if mastery_level:
                q = q.where(VocabUserWordState.mastery_level == mastery_level)
            rows = (
                await self.s.execute(
                    q.order_by(VocabUserWordState.last_studied_at.desc()).limit(limit)
                )
            ).scalars()
            out.extend(word_state_to_progress(r) for r in rows)
        if content_type in (None, "grammar"):
            q = select(GrammarLearningProgress).where(
                GrammarLearningProgress.user_id == uid
            )
            if mastery_level:
                q = q.where(GrammarLearningProgress.mastery_level == mastery_level)
            rows = (
                await self.s.execute(
                    q.order_by(GrammarLearningProgress.last_studied_at.desc()).limit(
                        limit
                    )
                )
            ).scalars()
            out.extend(grammar_progress_to_dict(r) for r in rows)
        return out[:limit]

    async def due_for_review(
        self, user_id: str, content_type: str, limit: int
    ) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        uid = _uuid(user_id)
        if content_type == "grammar":
            rows = (
                await self.s.execute(
                    select(GrammarLearningProgress)
                    .where(GrammarLearningProgress.user_id == uid)
                    .order_by(GrammarLearningProgress.last_studied_at.asc())
                    .limit(limit)
                )
            ).scalars()
            return [grammar_progress_to_dict(r) for r in rows]
        rows = (
            await self.s.execute(
                select(VocabUserWordState)
                .where(
                    VocabUserWordState.user_id == uid,
                    VocabUserWordState.due_at <= now,
                )
                .order_by(VocabUserWordState.due_at.asc())
                .limit(limit)
            )
        ).scalars()
        return [word_state_to_progress(r) for r in rows]

    async def get_one(
        self, *, user_id: str, content_id: str, content_type: str
    ) -> Optional[Dict[str, Any]]:
        uid = _uuid(user_id)
        if content_type == "grammar":
            row = (
                await self.s.execute(
                    select(GrammarLearningProgress).where(
                        GrammarLearningProgress.user_id == uid,
                        GrammarLearningProgress.structure_id == content_id,
                    )
                )
            ).scalar_one_or_none()
            return grammar_progress_to_dict(row) if row else None
        row = (
            await self.s.execute(
                select(VocabUserWordState).where(
                    VocabUserWordState.user_id == uid,
                    VocabUserWordState.word_id == content_id,
                )
            )
        ).scalar_one_or_none()
        return word_state_to_progress(row) if row else None

    async def list_for_content_ids(
        self, *, user_id: str, content_type: str, content_ids: List[str]
    ) -> List[Dict[str, Any]]:
        if not content_ids:
            return []
        uid = _uuid(user_id)
        if content_type == "grammar":
            rows = (
                await self.s.execute(
                    select(GrammarLearningProgress).where(
                        GrammarLearningProgress.user_id == uid,
                        GrammarLearningProgress.structure_id.in_(content_ids),
                    )
                )
            ).scalars()
            return [grammar_progress_to_dict(r) for r in rows]
        rows = (
            await self.s.execute(
                select(VocabUserWordState).where(
                    VocabUserWordState.user_id == uid,
                    VocabUserWordState.word_id.in_(content_ids),
                )
            )
        ).scalars()
        return [word_state_to_progress(r) for r in rows]

    async def list_all_vocab(
        self,
        user_id: str,
        *,
        limit: int = 50_000,
    ) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(VocabUserWordState)
                .where(VocabUserWordState.user_id == _uuid(user_id))
                .limit(limit)
            )
        ).scalars()
        return [word_state_to_progress(r) for r in rows]

    async def list_for_pack(
        self,
        *,
        user_id: str,
        pack_id: str,
        content_ids: List[str],
        limit: int = 20_000,
    ) -> List[Dict[str, Any]]:
        uid = _uuid(user_id)
        clauses = [VocabUserWordState.pack_id == pack_id]
        if content_ids:
            clauses.append(VocabUserWordState.word_id.in_(content_ids))
        rows = (
            await self.s.execute(
                select(VocabUserWordState)
                .where(VocabUserWordState.user_id == uid, or_(*clauses))
                .limit(limit)
            )
        ).scalars()
        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            doc = word_state_to_progress(row)
            seen[doc["content_id"]] = doc
        return list(seen.values())

    async def upsert_vocab_state(
        self,
        *,
        user_id: str,
        word_id: str,
        update_doc: Dict[str, Any],
    ) -> Dict[str, Any]:
        values = progress_to_word_state_values(user_id, word_id, update_doc)
        stmt = pg_insert(VocabUserWordState).values(**values)
        update_cols = {
            k: v
            for k, v in values.items()
            if k not in ("user_id", "word_id", "first_studied_at")
        }
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_vocab_user_word_state",
                set_=update_cols,
            )
        )
        return update_doc

    async def _save_grammar_progress(
        self, user_id: str, content_id: str, doc: Dict[str, Any]
    ) -> None:
        last_studied = doc.get("last_studied") or doc.get("updated_at")
        if isinstance(last_studied, str):
            try:
                last_studied = datetime.fromisoformat(
                    last_studied.replace("Z", "+00:00")
                )
            except ValueError:
                last_studied = datetime.utcnow()
        stmt = pg_insert(GrammarLearningProgress).values(
            user_id=_uuid(user_id),
            structure_id=content_id,
            mastery_level=doc.get("mastery_level") or "new",
            progress_data=doc,
            last_studied_at=last_studied,
        )
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_grammar_learning_progress",
                set_={
                    "mastery_level": doc.get("mastery_level") or "new",
                    "progress_data": doc,
                    "last_studied_at": last_studied,
                    "updated_at": datetime.utcnow(),
                },
            )
        )

    async def upsert_review(
        self,
        *,
        user_id: str,
        content_id: str,
        content_type: str,
        is_correct: bool,
        time_taken: int,
        exercise_type: str,
    ) -> Dict[str, Any]:
        existing = await self.get_one(
            user_id=user_id, content_id=content_id, content_type=content_type
        )
        now = datetime.utcnow()

        ease = 2.5
        interval = 1
        repetitions = 0
        current_streak = 0
        best_streak = 0
        correct = 0
        attempts = 0
        exercise_performance: Dict[str, Any] = {}

        if existing:
            sr = existing.get("spaced_repetition", {})
            ease = float(sr.get("ease_factor", 2.5))
            interval = int(sr.get("interval", 1))
            repetitions = int(sr.get("repetitions", 0))
            current_streak = int(existing.get("current_streak", 0))
            best_streak = int(existing.get("best_streak", 0))
            correct = int(existing.get("correct_answers", 0))
            attempts = int(existing.get("total_attempts", 0))
            exercise_performance = dict(existing.get("exercise_performance") or {})

        attempts += 1
        ep = dict(exercise_performance.get(exercise_type) or {})
        ep["attempts"] = int(ep.get("attempts") or 0) + 1
        ep["correct"] = int(ep.get("correct") or 0) + int(is_correct)
        ep["time_taken"] = int(ep.get("time_taken") or 0) + time_taken
        exercise_performance[exercise_type] = ep

        if is_correct:
            correct += 1
            repetitions += 1
            current_streak += 1
            best_streak = max(best_streak, current_streak)
            ease = max(1.3, ease + 0.1)
            interval = (
                1
                if repetitions == 1
                else (6 if repetitions == 2 else int(interval * ease))
            )
        else:
            repetitions = 0
            current_streak = 0
            ease = max(1.3, ease - 0.2)
            interval = 1

        next_review = now + timedelta(days=max(1, interval))
        accuracy = correct / attempts if attempts else 0
        if accuracy >= 0.9 and repetitions >= 5:
            mastery = "mastered"
        elif accuracy >= 0.7 and repetitions >= 2:
            mastery = "reviewing"
        elif attempts >= 1:
            mastery = "learning"
        else:
            mastery = "new"

        first_studied = (existing or {}).get("first_studied") or now
        update_doc = {
            "user_id": user_id,
            "content_id": content_id,
            "content_type": content_type,
            "mastery_level": mastery,
            "correct_answers": correct,
            "total_attempts": attempts,
            "current_streak": current_streak,
            "best_streak": best_streak,
            "exercise_performance": exercise_performance,
            "spaced_repetition": {
                "ease_factor": round(ease, 2),
                "interval": interval,
                "repetitions": repetitions,
                "last_reviewed": now,
                "next_review": next_review,
            },
            "first_studied": first_studied or now,
            "last_studied": now,
            "updated_at": now,
        }
        if content_type == "grammar":
            await self._save_grammar_progress(user_id, content_id, update_doc)
        else:
            await self.upsert_vocab_state(
                user_id=user_id, word_id=content_id, update_doc=update_doc
            )
        return update_doc
