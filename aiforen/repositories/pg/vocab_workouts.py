"""Persistence and quality-gated question lookup for adaptive vocab workouts."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import (
    UserLearningWeakness,
    VocabCoachingWorkout,
    VocabCoachingWorkoutItem,
    VocabLexeme,
    VocabQuestion,
    VocabUserSkillState,
    VocabUserWordState,
)
from aiforen.domain.vocab_workout import canonical_skill
from aiforen.repositories.pg.personalization import WEAKNESS_LABELS

ACTIVE_QUALITY_TIERS = ("good", "excellent", "elite")


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


class VocabWorkoutRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get_today(
        self, *, user_id: str, workout_date: date, track_id: str
    ) -> Optional[VocabCoachingWorkout]:
        return (
            await self.s.execute(
                select(VocabCoachingWorkout).where(
                    VocabCoachingWorkout.user_id == _uuid(user_id),
                    VocabCoachingWorkout.workout_date == workout_date,
                    VocabCoachingWorkout.track_id == track_id,
                )
            )
        ).scalar_one_or_none()

    async def get(
        self, *, user_id: str, workout_id: str
    ) -> Optional[VocabCoachingWorkout]:
        return (
            await self.s.execute(
                select(VocabCoachingWorkout).where(
                    VocabCoachingWorkout.id == _uuid(workout_id),
                    VocabCoachingWorkout.user_id == _uuid(user_id),
                )
            )
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: str,
        workout_date: date,
        track_id: str,
        cefr_level: str,
        focus_skill: str,
        intensity: str,
        estimated_minutes: int,
        coach_copy: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> VocabCoachingWorkout:
        workout = VocabCoachingWorkout(
            user_id=_uuid(user_id),
            workout_date=workout_date,
            track_id=track_id,
            cefr_level=cefr_level,
            focus_skill=focus_skill,
            intensity=intensity,
            estimated_minutes=estimated_minutes,
            coach_copy=coach_copy,
            progress={},
            summary={},
        )
        self.s.add(workout)
        await self.s.flush()
        for index, item in enumerate(items, start=1):
            self.s.add(
                VocabCoachingWorkoutItem(
                    workout_id=workout.id,
                    phase=str(item["phase"]),
                    order_index=index * 10,
                    word_id=str(item["word_id"]),
                    question_id=_uuid(item["question_id"]),
                    mastery_slot=int(item.get("mastery_slot") or 1),
                    skill_id=str(item["skill_id"]),
                    interaction_kind=str(item.get("interaction_kind") or "mcq"),
                    is_required=bool(item.get("is_required", True)),
                )
            )
        await self.s.flush()
        return workout

    async def list_items(
        self, workout_id: str | uuid.UUID
    ) -> List[VocabCoachingWorkoutItem]:
        return list(
            (
                await self.s.execute(
                    select(VocabCoachingWorkoutItem)
                    .where(VocabCoachingWorkoutItem.workout_id == _uuid(workout_id))
                    .order_by(VocabCoachingWorkoutItem.order_index)
                )
            )
            .scalars()
            .all()
        )

    async def get_item(
        self, *, workout_id: str, item_id: str
    ) -> Optional[VocabCoachingWorkoutItem]:
        return (
            await self.s.execute(
                select(VocabCoachingWorkoutItem).where(
                    VocabCoachingWorkoutItem.id == _uuid(item_id),
                    VocabCoachingWorkoutItem.workout_id == _uuid(workout_id),
                )
            )
        ).scalar_one_or_none()

    async def mark_started(self, workout: VocabCoachingWorkout) -> None:
        if workout.started_at is None:
            workout.started_at = datetime.now(timezone.utc)
        if workout.status == "ready":
            workout.status = "in_progress"

    async def mark_skipped(self, workout: VocabCoachingWorkout) -> None:
        if workout.status in ("completed", "skipped"):
            return
        now = datetime.now(timezone.utc)
        workout.status = "skipped"
        workout.completed_at = workout.completed_at or now
        if workout.started_at is None:
            workout.started_at = now
        workout.summary = {
            **(workout.summary or {}),
            "skipped": True,
            "skill_gain": workout.focus_skill,
            "correct": int((workout.progress or {}).get("correct") or 0),
            "total": int((workout.progress or {}).get("total") or 0),
        }
        await self.s.flush()
        await self.s.flush()

    async def complete_item(
        self,
        *,
        item: VocabCoachingWorkoutItem,
        attempt_id: str,
        result: Dict[str, Any],
    ) -> None:
        item.attempt_id = attempt_id
        item.result = _jsonable(result)
        item.status = "completed"
        await self.s.flush()

    async def insert_repair(
        self,
        *,
        workout_id: str,
        parent: VocabCoachingWorkoutItem,
        candidate: Dict[str, Any],
    ) -> VocabCoachingWorkoutItem:
        row = VocabCoachingWorkoutItem(
            workout_id=_uuid(workout_id),
            phase="focus",
            order_index=parent.order_index + 1,
            word_id=str(candidate["word_id"]),
            question_id=_uuid(candidate["question_id"]),
            mastery_slot=int(candidate.get("mastery_slot") or 1),
            skill_id=str(candidate["skill_id"]),
            interaction_kind=str(candidate.get("interaction_kind") or "mcq"),
            is_required=True,
            repair_parent_id=parent.id,
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def add_bonus_items(
        self, *, workout_id: str, candidates: List[Dict[str, Any]], limit: int = 3
    ) -> None:
        items = await self.list_items(workout_id)
        bonus_rows = [row for row in items if not row.is_required]
        if any(row.status != "completed" for row in bonus_rows):
            return
        if len(bonus_rows) >= limit:
            return
        order_index = max((row.order_index for row in items), default=0)
        used_questions = {str(row.question_id) for row in items}
        for candidate in candidates:
            if len([row for row in items if not row.is_required]) >= limit:
                break
            if str(candidate.get("question_id") or "") in used_questions:
                continue
            order_index += 10
            row = VocabCoachingWorkoutItem(
                workout_id=_uuid(workout_id),
                phase="bonus",
                order_index=order_index,
                word_id=str(candidate["word_id"]),
                question_id=_uuid(candidate["question_id"]),
                mastery_slot=int(candidate.get("mastery_slot") or 1),
                skill_id=str(candidate["skill_id"]),
                interaction_kind=str(candidate.get("interaction_kind") or "mcq"),
                is_required=False,
            )
            self.s.add(row)
            items.append(row)
            used_questions.add(str(candidate["question_id"]))
        await self.s.flush()

    async def update_progress(self, workout: VocabCoachingWorkout) -> None:
        items = await self.list_items(workout.id)
        required = [row for row in items if row.is_required]
        completed = [row for row in required if row.status == "completed"]
        correct = [
            row for row in completed if bool((row.result or {}).get("is_correct"))
        ]
        repairs = [row for row in items if row.repair_parent_id is not None]
        by_phase: Dict[str, Dict[str, int]] = {}
        for phase in ("warmup", "focus", "stretch"):
            phase_rows = [row for row in required if row.phase == phase]
            by_phase[phase] = {
                "completed": len(
                    [row for row in phase_rows if row.status == "completed"]
                ),
                "total": len(phase_rows),
            }
        workout.progress = {
            "completed": len(completed),
            "total": len(required),
            "correct": len(correct),
            "repairs": len(repairs),
            "phases": by_phase,
        }
        if required and len(completed) >= len(required):
            workout.status = "completed"
            workout.completed_at = workout.completed_at or datetime.now(timezone.utc)
            workout.summary = {
                "skill_gain": workout.focus_skill,
                "correct": len(correct),
                "total": len(required),
                "repairs_completed": len(repairs),
                "review_words": list(
                    dict.fromkeys(
                        row.word_id
                        for row in completed
                        if not bool((row.result or {}).get("is_correct"))
                    )
                ),
            }
        await self.s.flush()

    async def question_candidates(
        self, *, user_id: str, track_id: str, level_code: str, limit: int = 600
    ) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(VocabQuestion, VocabLexeme, VocabUserWordState)
                .join(VocabLexeme, VocabLexeme.id == VocabQuestion.lexeme_id)
                .outerjoin(
                    VocabUserWordState,
                    (VocabUserWordState.lexeme_id == VocabQuestion.lexeme_id)
                    & (VocabUserWordState.user_id == _uuid(user_id)),
                )
                .where(
                    VocabQuestion.track_id == track_id,
                    VocabQuestion.level_code == level_code,
                    VocabQuestion.status.in_(("validated", "approved")),
                    VocabQuestion.quality_tier.in_(ACTIVE_QUALITY_TIERS),
                )
                .order_by(
                    VocabUserWordState.due_at.asc().nullslast(),
                    VocabQuestion.mastery_slot,
                    VocabLexeme.id,
                )
                .limit(limit)
            )
        ).all()
        now = datetime.now(timezone.utc)
        return [
            {
                "question_id": str(question.id),
                "word_id": str(lexeme.id),
                "word": lexeme.display_word,
                "mastery_slot": int(question.mastery_slot or 1),
                "task_type": question.type,
                "skill": question.skill,
                "skill_id": canonical_skill(question.type, question.skill),
                "interaction_kind": question.interaction_kind,
                "prompt": question.prompt,
                "explanation": question.explanation,
                "payload": question.payload or {},
                "options": question.options or [],
                "correct_option_id": question.correct_option_id,
                "track_id": question.track_id,
                "level_code": question.level_code,
                "due_at": state.due_at.isoformat() if state and state.due_at else None,
                "is_due": bool(state and state.due_at and state.due_at <= now),
            }
            for question, lexeme, state in rows
        ]

    async def get_question_candidate(
        self, question_id: str | uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        row = (
            await self.s.execute(
                select(VocabQuestion, VocabLexeme)
                .join(VocabLexeme, VocabLexeme.id == VocabQuestion.lexeme_id)
                .where(VocabQuestion.id == _uuid(question_id))
            )
        ).one_or_none()
        if row is None:
            return None
        question, lexeme = row
        return {
            "question_id": str(question.id),
            "word_id": str(lexeme.id),
            "word": lexeme.display_word,
            "mastery_slot": int(question.mastery_slot or 1),
            "task_type": question.type,
            "skill": question.skill,
            "skill_id": canonical_skill(question.type, question.skill),
            "interaction_kind": question.interaction_kind,
            "prompt": question.prompt,
            "explanation": question.explanation,
            "payload": question.payload or {},
            "options": question.options or [],
            "correct_option_id": question.correct_option_id,
            "track_id": question.track_id,
            "level_code": question.level_code,
        }

    async def list_skill_states(self, user_id: str) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(VocabUserSkillState).where(
                    VocabUserSkillState.user_id == _uuid(user_id)
                )
            )
        ).scalars()
        return [
            {
                "skill_id": row.skill_id,
                "score": float(row.score or 0),
                "evidence_count": row.evidence_count,
                "due_at": row.due_at.isoformat() if row.due_at else None,
            }
            for row in rows
        ]

    async def record_skill_outcome(
        self, *, user_id: str, track_id: str, skill_id: str, is_correct: bool
    ) -> None:
        now = datetime.now(timezone.utc)
        values = {
            "user_id": _uuid(user_id),
            "track_id": track_id,
            "skill_id": skill_id,
            "score": 1 if is_correct else -1,
            "evidence_count": 1,
            "correct_count": 1 if is_correct else 0,
            "incorrect_count": 0 if is_correct else 1,
            "success_streak": 1 if is_correct else 0,
            "due_at": now + timedelta(days=3 if is_correct else 1),
            "last_seen_at": now,
            "updated_at": now,
        }
        stmt = pg_insert(VocabUserSkillState).values(**values)
        await self.s.execute(
            stmt.on_conflict_do_update(
                constraint="uq_vocab_user_skill_state",
                set_={
                    "score": VocabUserSkillState.score + (1 if is_correct else -1),
                    "evidence_count": VocabUserSkillState.evidence_count + 1,
                    "correct_count": VocabUserSkillState.correct_count
                    + (1 if is_correct else 0),
                    "incorrect_count": VocabUserSkillState.incorrect_count
                    + (0 if is_correct else 1),
                    "success_streak": (
                        VocabUserSkillState.success_streak + 1 if is_correct else 0
                    ),
                    "due_at": values["due_at"],
                    "last_seen_at": now,
                    "updated_at": now,
                },
            )
        )

    async def record_issue_outcome(
        self,
        *,
        user_id: str,
        skill_id: str,
        is_correct: bool,
        word_id: str,
    ) -> bool:
        uid = _uuid(user_id)
        label = WEAKNESS_LABELS.get(skill_id, skill_id.replace("_", " ").title())
        row = (
            await self.s.execute(
                select(UserLearningWeakness).where(
                    UserLearningWeakness.user_id == uid,
                    UserLearningWeakness.dimension == skill_id,
                    UserLearningWeakness.label == label,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row is None:
            if is_correct:
                return False
            row = UserLearningWeakness(
                user_id=uid,
                dimension=skill_id,
                label=label,
                severity=1,
                evidence_count=1,
                success_streak=0,
                last_seen_at=now,
                evidence={"word_id": word_id},
            )
            self.s.add(row)
            await self.s.flush()
            return False
        if is_correct:
            row.severity = max(0, float(row.severity or 0) - 0.5)
            row.success_streak = int(row.success_streak or 0) + 1
            if row.success_streak >= 2:
                row.resolved_at = now
                await self.s.flush()
                return True
        else:
            # submit_vocab_mcq already records the failure. The workout layer
            # owns decay/resolve state without counting the same attempt twice.
            row.success_streak = 0
            row.resolved_at = None
            row.last_seen_at = now
            row.evidence = {"word_id": word_id}
        row.updated_at = now
        await self.s.flush()
        return False

    async def count_repairs(self, workout_id: str) -> int:
        return int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(VocabCoachingWorkoutItem)
                    .where(
                        VocabCoachingWorkoutItem.workout_id == _uuid(workout_id),
                        VocabCoachingWorkoutItem.repair_parent_id.is_not(None),
                    )
                )
            ).scalar_one()
            or 0
        )
