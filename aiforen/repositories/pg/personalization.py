"""Postgres learner personalization repository.

This module is the bridge for the migration period: Mongo remains the legacy
progress store, while Postgres receives normalized events and compact state for
personalized vocab recommendations.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import (
    LearningEvent,
    PublicAssessment,
    UsageQuota,
    UserLearningDailyRollup,
    UserLearningWeakness,
    VocabCoachingWorkout,
    VocabCoachingWorkoutItem,
    VocabDailyMission,
    VocabLegacyWordMap,
    VocabUserPackState,
    VocabUserSkillState,
    VocabUserWordState,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


WEAKNESS_LABELS: Dict[str, str] = {
    "meaning": "word meaning",
    "context": "meaning in context",
    "collocation": "collocation",
    "pattern": "sentence pattern",
    "translation": "translation",
    "usage_correction": "usage correction",
    "register": "register and tone",
    "precision": "precision and nuance",
    "rewrite": "sentence rewrite",
    "meaning_mcq_wrong": "Meaning MCQ",
    "recall_failed": "word recall",
    "translation_failed": "sentence practice",
    "topic_sentence_failed": "sentence practice",
    "missing_target_word": "target word usage",
    "invalid_language": "English-only answer",
    "collocation_weak": "collocation",
    "low_mastery_band": "low mastery band",
    "stale_review_due": "due reviews",
    "weak_stat_label": "vocabulary group",
}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _maybe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


class LearningPersonalizationRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def resolve_lexeme_id(self, word_id: Optional[str]) -> Optional[uuid.UUID]:
        if not word_id:
            return None
        try:
            return uuid.UUID(str(word_id))
        except ValueError:
            pass
        stmt = select(VocabLegacyWordMap.lexeme_id).where(
            VocabLegacyWordMap.legacy_word_id == str(word_id)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def record_vocab_event(
        self,
        *,
        user_id: str | uuid.UUID,
        event_type: str,
        event_id: Optional[str] = None,
        word_id: Optional[str] = None,
        pack_id: Optional[str] = None,
        question_type: Optional[str] = None,
        step: Optional[str] = None,
        is_correct: Optional[bool] = None,
        score: Optional[float] = None,
        time_taken: int = 0,
        answer_meta: Optional[Dict[str, Any]] = None,
        ai_eval_meta: Optional[Dict[str, Any]] = None,
        weakness_tags: Optional[List[str]] = None,
        occurred_at: Optional[datetime] = None,
        progress: Optional[Dict[str, Any]] = None,
        word: Optional[Dict[str, Any]] = None,
        pack_mastery_pct: Optional[float] = None,
        workout_id: Optional[str | uuid.UUID] = None,
        workout_item_id: Optional[str | uuid.UUID] = None,
        skill_id: Optional[str] = None,
        mastery_slot: Optional[int] = None,
        interaction_kind: Optional[str] = None,
    ) -> None:
        uid = _uuid(user_id)
        now = occurred_at or datetime.now(VN_TZ)
        weakness_tags = list(dict.fromkeys(weakness_tags or []))
        lexeme_id = await self.resolve_lexeme_id(word_id)

        event_result = await self.s.execute(
            pg_insert(LearningEvent)
            .values(
                event_id=event_id or f"lev_{secrets.token_urlsafe(12)}",
                user_id=uid,
                event_type=event_type,
                source_content_type="vocabulary",
                content_type="vocabulary",
                word_id=word_id,
                lexeme_id=lexeme_id,
                pack_id=pack_id,
                question_type=question_type,
                step=step,
                is_correct=is_correct,
                score=score,
                time_taken=max(0, int(time_taken or 0)),
                answer_meta=answer_meta or {},
                ai_eval_meta=ai_eval_meta or {},
                weakness_tags=weakness_tags,
                workout_id=_uuid(workout_id) if workout_id else None,
                workout_item_id=_uuid(workout_item_id) if workout_item_id else None,
                skill_id=skill_id,
                mastery_slot=mastery_slot,
                interaction_kind=interaction_kind,
                occurred_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_learning_event_id")
            .returning(LearningEvent.id)
        )
        inserted_event = event_result.scalar_one_or_none() is not None
        if word_id and progress is not None:
            await self._upsert_word_state(
                user_id=uid,
                word_id=word_id,
                lexeme_id=lexeme_id,
                pack_id=pack_id
                or (word or {}).get("pack_id")
                or progress.get("pack_id"),
                progress=progress,
                weakness_tags=weakness_tags,
                now=now,
            )
        if pack_id:
            await self._upsert_pack_state(
                user_id=uid,
                pack_id=pack_id,
                pack_mastery_pct=pack_mastery_pct,
                word=word,
                now=now,
            )
        if inserted_event:
            await self._upsert_daily_rollup(
                user_id=uid,
                day=now.astimezone(VN_TZ).date() if now.tzinfo else now.date(),
                event_type=event_type,
                pack_id=pack_id,
                is_correct=is_correct,
                time_taken=time_taken,
                weakness_tags=weakness_tags,
                word=word,
            )
            for tag in weakness_tags:
                await self._upsert_weakness(
                    user_id=uid,
                    tag=tag,
                    pack_id=pack_id,
                    word=word,
                    now=now,
                )

    async def _upsert_word_state(
        self,
        *,
        user_id: uuid.UUID,
        word_id: str,
        lexeme_id: Optional[uuid.UUID],
        pack_id: Optional[str],
        progress: Dict[str, Any],
        weakness_tags: List[str],
        now: datetime,
    ) -> None:
        sr = progress.get("spaced_repetition") or {}
        last_result = _jsonable(
            {
                "last_mcq_result": progress.get("last_mcq_result"),
                "last_sentence_id": progress.get("last_sentence_id"),
                "event_at": now.isoformat(),
            }
        )
        values = {
            "user_id": user_id,
            "word_id": word_id,
            "lexeme_id": lexeme_id,
            "pack_id": pack_id,
            "mastery_level": progress.get("mastery_level") or "new",
            "mastery_step": int(progress.get("mastery_step") or 0),
            "mastery_point_pct": float(progress.get("mastery_point_pct") or 0),
            "due_at": sr.get("next_review"),
            "failed_locked_until": progress.get("failed_locked_until"),
            "marked_known": bool(progress.get("marked_known")),
            "best_translate_pct": float(progress.get("best_translate_pct") or 0),
            "best_topic_pct": float(progress.get("best_topic_pct") or 0),
            "last_result": last_result,
            "weakness_tags": weakness_tags,
            "first_studied_at": progress.get("first_studied")
            or progress.get("created_at"),
            "last_studied_at": progress.get("last_studied") or now,
            "updated_at": now,
        }
        stmt = pg_insert(VocabUserWordState).values(**values)
        update_values = {
            k: v
            for k, v in values.items()
            if k not in ("user_id", "word_id", "first_studied_at")
        }
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_vocab_user_word_state",
                set_=update_values,
            )
        )

    async def _upsert_pack_state(
        self,
        *,
        user_id: uuid.UUID,
        pack_id: str,
        pack_mastery_pct: Optional[float],
        word: Optional[Dict[str, Any]],
        now: datetime,
    ) -> None:
        band = _maybe_float((word or {}).get("band_score"))
        values = {
            "user_id": user_id,
            "pack_id": pack_id,
            "mastery_pct": float(pack_mastery_pct or 0),
            "focus_band": band,
            "active_band_meta": {
                "band_score": band,
                "category": (word or {}).get("category"),
                "stat_labels": (word or {}).get("stat_labels") or [],
            },
            "last_studied_at": now,
            "updated_at": now,
        }
        stmt = pg_insert(VocabUserPackState).values(**values)
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_vocab_user_pack_state",
                set_={
                    k: v for k, v in values.items() if k not in ("user_id", "pack_id")
                },
            )
        )

    async def _upsert_daily_rollup(
        self,
        *,
        user_id: uuid.UUID,
        day: date,
        event_type: str,
        pack_id: Optional[str],
        is_correct: Optional[bool],
        time_taken: int,
        weakness_tags: List[str],
        word: Optional[Dict[str, Any]],
    ) -> None:
        stmt = select(UserLearningDailyRollup).where(
            UserLearningDailyRollup.user_id == user_id,
            UserLearningDailyRollup.day == day,
        )
        row = (await self.s.execute(stmt)).scalar_one_or_none()
        if not row:
            row = UserLearningDailyRollup(user_id=user_id, day=day)
            self.s.add(row)
            await self.s.flush()
        action_counts = dict(row.action_counts or {})
        action_counts[event_type] = int(action_counts.get(event_type, 0)) + 1
        weak_dimensions = dict(row.weak_dimensions or {})
        for tag in weakness_tags:
            weak_dimensions[tag] = int(weak_dimensions.get(tag, 0)) + 1
        pack_counts = dict(row.pack_counts or {})
        if pack_id:
            pack_counts[pack_id] = int(pack_counts.get(pack_id, 0)) + 1
        category_counts = dict(row.category_counts or {})
        category = (word or {}).get("category")
        if category:
            category_counts[str(category)] = (
                int(category_counts.get(str(category), 0)) + 1
            )
        row.action_counts = action_counts
        row.weak_dimensions = weak_dimensions
        row.pack_counts = pack_counts
        row.category_counts = category_counts
        row.focus_band = _maybe_float((word or {}).get("band_score")) or row.focus_band
        row.correct_count = int(row.correct_count or 0) + (
            1 if is_correct is True else 0
        )
        row.incorrect_count = int(row.incorrect_count or 0) + (
            1 if is_correct is False else 0
        )
        row.total_time_taken = int(row.total_time_taken or 0) + max(
            0, int(time_taken or 0)
        )
        row.updated_at = datetime.now(VN_TZ)

    async def _upsert_weakness(
        self,
        *,
        user_id: uuid.UUID,
        tag: str,
        pack_id: Optional[str],
        word: Optional[Dict[str, Any]],
        now: datetime,
    ) -> None:
        label = WEAKNESS_LABELS.get(tag, tag.replace("_", " ").title())
        stat_labels = (word or {}).get("stat_labels") or []
        values = {
            "user_id": user_id,
            "dimension": tag,
            "label": label,
            "severity": 1.0,
            "evidence_count": 1,
            "last_seen_at": now,
            "recommended_action_type": "repair_weakness",
            "pack_id": pack_id,
            "stat_label": stat_labels[0] if stat_labels else None,
            "band": _maybe_float((word or {}).get("band_score")),
            "evidence": {
                "word_id": (word or {}).get("word_id"),
                "word": (word or {}).get("word"),
                "stat_labels": stat_labels,
            },
            "updated_at": now,
        }
        stmt = pg_insert(UserLearningWeakness).values(**values)
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_user_learning_weakness",
                set_={
                    "severity": UserLearningWeakness.severity + 1,
                    "evidence_count": UserLearningWeakness.evidence_count + 1,
                    "last_seen_at": now,
                    "pack_id": pack_id,
                    "stat_label": values["stat_label"],
                    "band": values["band"],
                    "evidence": values["evidence"],
                    "resolved_at": None,
                    "updated_at": now,
                },
            )
        )

    async def recent_actions(
        self, user_id: str | uuid.UUID, *, limit: int = 12
    ) -> List[Dict[str, Any]]:
        uid = _uuid(user_id)
        stmt = (
            select(LearningEvent)
            .where(
                LearningEvent.user_id == uid, LearningEvent.content_type == "vocabulary"
            )
            .order_by(desc(LearningEvent.occurred_at))
            .limit(limit)
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [
            {
                "event_type": row.event_type,
                "content_type": row.content_type,
                "word_id": row.word_id,
                "pack_id": row.pack_id,
                "question_type": row.question_type,
                "step": row.step,
                "is_correct": row.is_correct,
                "score": float(row.score) if row.score is not None else None,
                "time_taken": row.time_taken,
                "answer_meta": _jsonable(row.answer_meta or {}),
                "ai_eval_meta": _jsonable(row.ai_eval_meta or {}),
                "weakness_tags": list(row.weakness_tags or []),
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
            for row in rows
        ]

    async def top_weaknesses(
        self, user_id: str | uuid.UUID, *, limit: int = 5
    ) -> List[Dict[str, Any]]:
        uid = _uuid(user_id)
        stmt = (
            select(UserLearningWeakness)
            .where(
                UserLearningWeakness.user_id == uid,
                UserLearningWeakness.resolved_at.is_(None),
            )
            .order_by(
                desc(UserLearningWeakness.severity),
                desc(UserLearningWeakness.last_seen_at),
            )
            .limit(limit)
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [
            {
                "dimension": row.dimension,
                "label": row.label,
                "severity": float(row.severity or 0),
                "evidence_count": int(row.evidence_count or 0),
                "last_seen_at": (
                    row.last_seen_at.isoformat() if row.last_seen_at else None
                ),
                "recommended_action_type": row.recommended_action_type,
                "pack_id": row.pack_id,
                "stat_label": row.stat_label,
                "band": float(row.band) if row.band is not None else None,
                "evidence": _jsonable(row.evidence or {}),
                "suggested_repair": self.suggested_repair(row.dimension),
            }
            for row in rows
        ]

    @staticmethod
    def suggested_repair(dimension: str) -> str:
        return {
            "meaning_mcq_wrong": "Redo meaning MCQs before production practice.",
            "meaning": "Confirm the meaning, then use the word in a fresh context.",
            "context": "Practice choosing the correct sense from a real sentence.",
            "collocation": "Review collocations and produce one natural phrase.",
            "pattern": "Repeat the target sentence pattern with a short example.",
            "translation": "Write one clean translation sentence using the target word.",
            "usage_correction": "Correct one realistic usage error before moving on.",
            "register": "Compare tone and register in one focused example.",
            "precision": "Contrast the target with a near-synonym in context.",
            "rewrite": "Rewrite one sentence naturally with the target word.",
            "translation_failed": "Write one clean translation sentence using the target word.",
            "topic_sentence_failed": "Answer a short IELTS-style topic prompt with the target word.",
            "missing_target_word": "Repeat the sentence task and explicitly include the target word.",
            "invalid_language": "Rewrite the answer in English only.",
            "collocation_weak": "Review collocations and produce one natural phrase.",
            "stale_review_due": "Clear due reviews before learning new words.",
            "weak_stat_label": "Study a small pack from this vocabulary group.",
        }.get(dimension, "Practice this weak area in a short focused block.")

    async def get_daily_mission(
        self,
        *,
        user_id: str | uuid.UUID,
        mission_date: date,
        locale: str,
    ) -> Optional[Dict[str, Any]]:
        uid = _uuid(user_id)
        stmt = select(VocabDailyMission).where(
            VocabDailyMission.user_id == uid,
            VocabDailyMission.mission_date == mission_date,
            VocabDailyMission.locale == locale,
        )
        row = (await self.s.execute(stmt)).scalar_one_or_none()
        if not row:
            return None
        return {
            "snapshot_hash": row.snapshot_hash,
            "output": _jsonable(row.output or {}),
            "status": row.status,
            "model_provider": row.model_provider,
            "model_name": row.model_name,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "refresh_count": int(row.refresh_count or 0),
        }

    async def list_recent_daily_missions(
        self,
        *,
        user_id: str | uuid.UUID,
        before_date: date,
        locale: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        uid = _uuid(user_id)
        stmt = (
            select(VocabDailyMission)
            .where(
                VocabDailyMission.user_id == uid,
                VocabDailyMission.locale == locale,
                VocabDailyMission.mission_date < before_date,
            )
            .order_by(desc(VocabDailyMission.mission_date))
            .limit(max(1, min(int(limit or 3), 7)))
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [
            {
                "mission_date": row.mission_date.isoformat(),
                "status": row.status,
                "output": _jsonable(row.output or {}),
                "generated_at": (
                    row.generated_at.isoformat() if row.generated_at else None
                ),
            }
            for row in rows
        ]

    async def reset_user_learning_data(
        self, user_id: str | uuid.UUID
    ) -> Dict[str, int]:
        """Delete Postgres learner state; preserves subscriptions and account."""
        uid = _uuid(user_id)
        cleared: Dict[str, int] = {}

        async def _wipe(model: Any, key: str) -> None:
            result = await self.s.execute(delete(model).where(model.user_id == uid))
            cleared[key] = int(result.rowcount or 0)

        await _wipe(LearningEvent, "learning_events")
        workout_ids = select(VocabCoachingWorkout.id).where(
            VocabCoachingWorkout.user_id == uid
        )
        result = await self.s.execute(
            delete(VocabCoachingWorkoutItem).where(
                VocabCoachingWorkoutItem.workout_id.in_(workout_ids)
            )
        )
        cleared["vocab_coaching_workout_items"] = int(result.rowcount or 0)
        await _wipe(VocabCoachingWorkout, "vocab_coaching_workouts")
        await _wipe(VocabUserWordState, "vocab_user_word_state")
        await _wipe(VocabUserPackState, "vocab_user_pack_state")
        await _wipe(VocabUserSkillState, "vocab_user_skill_state")
        await _wipe(UserLearningDailyRollup, "user_learning_daily_rollups")
        await _wipe(UserLearningWeakness, "user_learning_weaknesses")
        await _wipe(VocabDailyMission, "vocab_daily_missions")
        await _wipe(UsageQuota, "usage_quota")
        await _wipe(PublicAssessment, "public_assessments")
        return cleared

    async def delete_daily_missions(
        self,
        *,
        user_id: str | uuid.UUID,
        mission_date: date | None = None,
    ) -> int:
        """Remove cached vocab missions (e.g. on login to force regeneration)."""
        uid = _uuid(user_id)
        stmt = delete(VocabDailyMission).where(VocabDailyMission.user_id == uid)
        if mission_date is not None:
            stmt = stmt.where(VocabDailyMission.mission_date == mission_date)
        result = await self.s.execute(stmt)
        return int(result.rowcount or 0)

    async def upsert_daily_mission(
        self,
        *,
        user_id: str | uuid.UUID,
        mission_date: date,
        locale: str,
        snapshot_hash: str,
        output: Dict[str, Any],
        status: str,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        error_meta: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        now = datetime.now(VN_TZ)
        values = {
            "user_id": _uuid(user_id),
            "mission_date": mission_date,
            "locale": locale,
            "snapshot_hash": snapshot_hash,
            "output": output,
            "model_provider": model_provider,
            "model_name": model_name,
            "status": status,
            "error_meta": error_meta or {},
            "generated_at": now,
            "expires_at": expires_at,
            "updated_at": now,
        }
        stmt = pg_insert(VocabDailyMission).values(**values)
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_vocab_daily_mission",
                set_={
                    **{
                        k: v
                        for k, v in values.items()
                        if k not in ("user_id", "mission_date", "locale")
                    },
                    "refresh_count": VocabDailyMission.refresh_count + 1,
                },
            )
        )
