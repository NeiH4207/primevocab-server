"""Adaptive daily vocab workout orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.vocab_learner_rhythm import classify_learner_rhythm
from aiforen.domain.vocab_workout import (
    INTENSITY_MINUTES,
    choose_focus_skill,
    compose_workout_items,
    intensity_for,
    select_micro_repair,
    workout_copy,
)
from aiforen.repositories.pg.personalization import LearningPersonalizationRepo
from aiforen.repositories.pg.vocab_workouts import VocabWorkoutRepo
from aiforen.services.learning_service import LearningService

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _cefr_level(profile: Dict[str, Any]) -> str:
    calibrated = str(profile.get("calibration_cefr_level") or "").strip().upper()
    if calibrated in {"A1", "A2", "B1", "B2", "C1", "C2"}:
        return calibrated
    band = float(profile.get("current_band") or 6.0)
    if band < 4.5:
        return "A1"
    if band < 5.5:
        return "A2"
    if band < 6.5:
        return "B1"
    if band < 7.5:
        return "B2"
    if band < 8.5:
        return "C1"
    return "C2"


def _quiz_step(candidate: Dict[str, Any]) -> Dict[str, Any]:
    interaction = str(candidate.get("interaction_kind") or "mcq")
    step = {
        "question_id": candidate["question_id"],
        "mastery_slot": int(candidate.get("mastery_slot") or 1),
        "track_id": candidate.get("track_id"),
        "task_type": candidate.get("task_type"),
        "skill": candidate.get("skill_id"),
        "level_code": candidate.get("level_code"),
        "interaction_kind": interaction,
        "prompt": candidate.get("prompt") or "",
        "explanation": candidate.get("explanation"),
        "payload": candidate.get("payload") or {},
    }
    payload = step["payload"]
    if isinstance(payload, dict):
        ctx = str(payload.get("context") or "").strip()
        if ctx:
            step["context"] = ctx
    if interaction == "mcq":
        step["mcq"] = {
            "question": candidate.get("prompt") or "",
            "options": candidate.get("options") or [],
            "correct_option_id": candidate.get("correct_option_id") or "",
            "explanation": candidate.get("explanation"),
        }
    return step


class VocabWorkoutService:
    def __init__(self, pg: AsyncSession):
        self.pg = pg
        self.repo = VocabWorkoutRepo(pg)
        self.personalization = LearningPersonalizationRepo(pg)
        self.learning = LearningService(pg)

    async def today(self, *, user_id: str, plan_code: str = "free") -> Dict[str, Any]:
        today = datetime.now(VN_TZ).date()
        profile = await self.learning.get_vocab_profile(user_id)
        level = _cefr_level(profile)
        track_id = f"cefr:{level}"
        existing = await self.repo.get_today(
            user_id=user_id, workout_date=today, track_id=track_id
        )
        if existing is not None:
            return await self.serialize(existing, user_id=user_id)

        stats = await self.learning.get_vocab_stats(user_id, plan_code=plan_code)
        daily_counts = stats.get("vocab_daily_counts") or {}
        rhythm = classify_learner_rhythm(
            daily_counts=daily_counts,
            today=today,
            total_progress_words=int(stats.get("total_progress_words") or 0),
            learned_today=int(stats.get("learned_today") or 0),
        )
        due_today = int(stats.get("due_today") or 0)
        weaknesses = await self.personalization.top_weaknesses(user_id, limit=8)
        skill_states = await self.repo.list_skill_states(user_id)
        focus_skill = choose_focus_skill(
            weaknesses=weaknesses, skill_states=skill_states, due_today=due_today
        )
        intensity = intensity_for(
            learner_rhythm=rhythm,
            due_today=due_today,
            daily_goal=int(profile.get("daily_goal") or 5),
        )
        candidates = await self.repo.question_candidates(
            user_id=user_id, track_id=track_id, level_code=level
        )
        items = compose_workout_items(
            candidates=candidates,
            focus_skill=focus_skill,
            intensity=intensity,
        )
        workout = await self.repo.create(
            user_id=user_id,
            workout_date=today,
            track_id=track_id,
            cefr_level=level,
            focus_skill=focus_skill,
            intensity=intensity,
            estimated_minutes=INTENSITY_MINUTES[intensity],
            coach_copy=workout_copy(
                focus_skill=focus_skill, intensity=intensity, due_today=due_today
            ),
            items=items,
        )
        await self.repo.update_progress(workout)
        return await self.serialize(workout, user_id=user_id)

    async def get(self, *, user_id: str, workout_id: str) -> Dict[str, Any]:
        workout = await self.repo.get(user_id=user_id, workout_id=workout_id)
        if workout is None:
            raise ValueError("Workout not found")
        return await self.serialize(workout, user_id=user_id)

    async def start(self, *, user_id: str, workout_id: str) -> Dict[str, Any]:
        workout = await self.repo.get(user_id=user_id, workout_id=workout_id)
        if workout is None:
            raise ValueError("Workout not found")
        await self.repo.mark_started(workout)
        await self.personalization.record_vocab_event(
            user_id=user_id,
            event_type="workout_started",
            workout_id=workout.id,
        )
        return await self.serialize(workout, user_id=user_id)

    async def answer(
        self,
        *,
        user_id: str,
        plan_code: str,
        workout_id: str,
        item_id: str,
        attempt_id: str,
        selected_option_id: Optional[str] = None,
        free_text_answer: Optional[str] = None,
        reorder_order: Optional[List[int]] = None,
        time_taken: int = 0,
    ) -> Dict[str, Any]:
        workout = await self.repo.get(user_id=user_id, workout_id=workout_id)
        if workout is None:
            raise ValueError("Workout not found")
        item = await self.repo.get_item(workout_id=workout_id, item_id=item_id)
        if item is None:
            raise ValueError("Workout item not found")
        if item.status == "completed":
            if item.attempt_id != attempt_id:
                raise ValueError("Workout item already answered")
            return {
                "workout": await self.serialize(workout, user_id=user_id),
                "answer_result": item.result,
                "repair_inserted": False,
                "issue_resolved": False,
                "idempotent_replay": True,
            }

        result = await self.learning.submit_vocab_mcq(
            user_id=user_id,
            plan_code=plan_code,
            word_id=item.word_id,
            selected_option_id=selected_option_id,
            question_id=str(item.question_id),
            free_text_answer=free_text_answer,
            reorder_order=reorder_order,
            time_taken=time_taken,
            workout_id=workout_id,
            workout_item_id=item_id,
            skill_id=item.skill_id,
            mastery_slot=item.mastery_slot,
            interaction_kind=item.interaction_kind,
        )
        stored_result = {
            "word_id": result.get("word_id"),
            "is_correct": bool(result.get("is_correct")),
            "correct_option_id": result.get("correct_option_id"),
            "ai_feedback": result.get("ai_feedback"),
            "ai_eval_failed": bool(result.get("ai_eval_failed")),
            "ai_quota_exceeded": bool(result.get("ai_quota_exceeded")),
            "upgrade_hint": result.get("upgrade_hint"),
        }
        await self.repo.complete_item(
            item=item, attempt_id=attempt_id, result=stored_result
        )
        await self.repo.record_skill_outcome(
            user_id=user_id,
            track_id=workout.track_id,
            skill_id=item.skill_id,
            is_correct=bool(result.get("is_correct")),
        )
        issue_resolved = await self.repo.record_issue_outcome(
            user_id=user_id,
            skill_id=item.skill_id,
            is_correct=bool(result.get("is_correct")),
            word_id=item.word_id,
        )

        repair_inserted = False
        if not result.get("is_correct"):
            repairs = await self.repo.count_repairs(workout_id)
            candidates = await self.repo.question_candidates(
                user_id=user_id,
                track_id=workout.track_id,
                level_code=workout.cefr_level,
            )
            repair = select_micro_repair(
                candidates=candidates,
                failed_item={
                    "question_id": str(item.question_id),
                    "skill_id": item.skill_id,
                },
                existing_repairs=repairs,
            )
            if repair is not None:
                await self.repo.insert_repair(
                    workout_id=workout_id, parent=item, candidate=repair
                )
                repair_inserted = True
        await self.repo.update_progress(workout)
        return {
            "workout": await self.serialize(workout, user_id=user_id),
            "answer_result": {**result, **stored_result},
            "repair_inserted": repair_inserted,
            "issue_resolved": issue_resolved,
            "idempotent_replay": False,
        }

    async def bonus(self, *, user_id: str, workout_id: str) -> Dict[str, Any]:
        workout = await self.repo.get(user_id=user_id, workout_id=workout_id)
        if workout is None:
            raise ValueError("Workout not found")
        if workout.status != "completed":
            raise ValueError(
                "Complete the required workout before starting bonus practice"
            )
        candidates = await self.repo.question_candidates(
            user_id=user_id,
            track_id=workout.track_id,
            level_code=workout.cefr_level,
        )
        await self.repo.add_bonus_items(workout_id=workout_id, candidates=candidates)
        return await self.serialize(workout, user_id=user_id)

    async def serialize(self, workout: Any, *, user_id: str) -> Dict[str, Any]:
        rows = await self.repo.list_items(workout.id)
        items: List[Dict[str, Any]] = []
        for row in rows:
            candidate = await self.repo.get_question_candidate(row.question_id)
            if candidate is None:
                continue
            items.append(
                {
                    "id": str(row.id),
                    "phase": row.phase,
                    "order_index": row.order_index,
                    "word_id": row.word_id,
                    "word": candidate.get("word"),
                    "mastery_slot": row.mastery_slot,
                    "skill_id": row.skill_id,
                    "interaction_kind": row.interaction_kind,
                    "is_required": row.is_required,
                    "status": row.status,
                    "result": row.result or {},
                    "repair_parent_id": (
                        str(row.repair_parent_id) if row.repair_parent_id else None
                    ),
                    "quiz_step": _quiz_step(candidate),
                }
            )
        current_item = next(
            (item for item in items if item["status"] == "pending"), None
        )
        return {
            "id": str(workout.id),
            "workout_date": workout.workout_date.isoformat(),
            "track_id": workout.track_id,
            "cefr_level": workout.cefr_level,
            "status": workout.status,
            "focus_skill": workout.focus_skill,
            "intensity": workout.intensity,
            "estimated_minutes": workout.estimated_minutes,
            "coach_copy": workout.coach_copy or {},
            "progress": workout.progress or {},
            "summary": workout.summary or {},
            "started_at": (
                workout.started_at.isoformat() if workout.started_at else None
            ),
            "completed_at": (
                workout.completed_at.isoformat() if workout.completed_at else None
            ),
            "items": items,
            "current_item": current_item,
        }
