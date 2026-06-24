"""Aggregate learner stats on Postgres."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import UserLearningStats
from aiforen.domain.vocab_daily_streak import (
    compute_daily_streak,
    vocab_counts_from_daily_activity,
)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_CEFR_DEFAULT_BAND: Dict[str, float] = {
    "A1": 3.0,
    "A2": 4.0,
    "B1": 5.5,
    "B2": 6.5,
    "C1": 7.5,
    "C2": 8.5,
}


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _row_to_dict(row: UserLearningStats) -> Dict[str, Any]:
    profile = dict(row.vocab_profile or {})
    if profile and "pack_mastery" not in profile:
        profile.setdefault("pack_mastery", {})
    return {
        "user_id": str(row.user_id),
        "grammar_total_learned": row.grammar_total_learned,
        "grammar_mastered": row.grammar_mastered,
        "grammar_accuracy": float(row.grammar_accuracy or 0),
        "grammar_current_streak": row.grammar_current_streak,
        "grammar_best_streak": row.grammar_best_streak,
        "vocab_total_learned": row.vocab_total_learned,
        "vocab_mastered": row.vocab_mastered,
        "vocab_accuracy": float(row.vocab_accuracy or 0),
        "vocab_current_streak": row.vocab_current_streak,
        "vocab_best_streak": row.vocab_best_streak,
        "total_study_time": row.total_study_time,
        "today_study_time": row.today_study_time,
        "estimated_grammar_band": float(row.estimated_grammar_band or 5),
        "estimated_vocab_band": float(row.estimated_vocab_band or 5),
        "vocab_profile": profile,
        "daily_activity": dict(row.daily_activity or {}),
        "last_activity": row.last_activity,
        "updated_at": row.updated_at,
        "created_at": row.created_at,
    }


class UserStatsRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def reset_for_user(self, user_id: str) -> int:
        from sqlalchemy import delete

        result = await self.s.execute(
            delete(UserLearningStats).where(UserLearningStats.user_id == _uuid(user_id))
        )
        return int(result.rowcount or 0)

    async def get_or_default(self, user_id: str) -> Dict[str, Any]:
        row = await self.s.get(UserLearningStats, _uuid(user_id))
        if row:
            return _row_to_dict(row)
        now = datetime.utcnow()
        defaults = {
            "user_id": _uuid(user_id),
            "grammar_total_learned": 0,
            "grammar_mastered": 0,
            "grammar_accuracy": 0,
            "grammar_current_streak": 0,
            "grammar_best_streak": 0,
            "vocab_total_learned": 0,
            "vocab_mastered": 0,
            "vocab_accuracy": 0,
            "vocab_current_streak": 0,
            "vocab_best_streak": 0,
            "total_study_time": 0,
            "today_study_time": 0,
            "estimated_grammar_band": 5.0,
            "estimated_vocab_band": 5.0,
            "vocab_profile": {},
            "daily_activity": {},
            "last_activity": now,
            "created_at": now,
            "updated_at": now,
        }
        await self.s.execute(pg_insert(UserLearningStats).values(**defaults))
        return _row_to_dict(await self.s.get(UserLearningStats, _uuid(user_id)))  # type: ignore[arg-type]

    async def _update(self, user_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.utcnow()
        await self.s.execute(
            pg_insert(UserLearningStats)
            .values(user_id=_uuid(user_id), **fields)
            .on_conflict_do_update(
                index_elements=[UserLearningStats.user_id],
                set_=fields,
            )
        )

    async def update_vocab_profile(
        self,
        user_id: str,
        *,
        current_band: float,
        target_band: float,
        daily_goal: int,
    ) -> Dict[str, Any]:
        existing = await self.get_or_default(user_id)
        profile = dict(existing.get("vocab_profile") or {})
        profile.update(
            {
                "current_band": current_band,
                "target_band": target_band,
                "daily_goal": daily_goal,
            }
        )
        await self._update(
            user_id,
            vocab_profile=profile,
            estimated_vocab_band=current_band,
        )
        return await self.get_or_default(user_id)

    async def mark_vocab_calibration_completed(
        self,
        user_id: str,
        *,
        cefr_level: str | None = None,
        estimated_band: float | None = None,
        calibration_insight: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        existing = await self.get_or_default(user_id)
        profile = dict(existing.get("vocab_profile") or {})
        profile["calibration_completed"] = True
        profile["calibration_completed_at"] = datetime.utcnow().isoformat()
        if cefr_level:
            profile["calibration_cefr_level"] = cefr_level
        if calibration_insight:
            profile["calibration_insight"] = calibration_insight
        updates: Dict[str, Any] = {"vocab_profile": profile}
        if estimated_band is not None:
            profile["current_band"] = float(estimated_band)
            updates["estimated_vocab_band"] = float(estimated_band)
            updates["vocab_profile"] = profile
        await self._update(user_id, **updates)
        return await self.get_or_default(user_id)

    async def clear_vocab_calibration(self, user_id: str) -> Dict[str, Any]:
        """Reset the quick-check flag so the calibration screen reappears."""
        existing = await self.get_or_default(user_id)
        profile = dict(existing.get("vocab_profile") or {})
        for key in (
            "calibration_completed",
            "calibration_completed_at",
            "calibration_cefr_level",
            "calibration_insight",
            "level_source",
            "level_changed_at",
        ):
            profile.pop(key, None)
        await self._update(user_id, vocab_profile=profile)
        return await self.get_or_default(user_id)

    async def set_vocab_coaching_level(
        self,
        user_id: str,
        *,
        cefr_level: str,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """Set coaching CEFR without forcing a full calibration reset."""
        existing = await self.get_or_default(user_id)
        profile = dict(existing.get("vocab_profile") or {})
        profile["calibration_completed"] = True
        profile["calibration_cefr_level"] = cefr_level
        profile["level_source"] = source
        profile["level_changed_at"] = datetime.utcnow().isoformat()
        if not profile.get("calibration_completed_at"):
            profile["calibration_completed_at"] = profile["level_changed_at"]
        band = float(_CEFR_DEFAULT_BAND.get(cefr_level.upper(), 5.5))
        profile["current_band"] = band
        await self._update(
            user_id,
            vocab_profile=profile,
            estimated_vocab_band=band,
        )
        return await self.get_or_default(user_id)

    def _pack_mastery_map(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        profile = stats.get("vocab_profile") or {}
        raw = profile.get("pack_mastery")
        return dict(raw) if isinstance(raw, dict) else {}

    async def get_pack_mastery(self, user_id: str, pack_id: str) -> Dict[str, Any]:
        stats = await self.get_or_default(user_id)
        row = self._pack_mastery_map(stats).get(pack_id) or {}
        return {
            "stored_pct": float(row.get("stored_pct") or 0.0),
            "last_decay_date": row.get("last_decay_date"),
        }

    async def set_pack_mastery(
        self,
        user_id: str,
        pack_id: str,
        *,
        stored_pct: float,
        last_decay_date: str,
    ) -> Dict[str, Any]:
        stats = await self.get_or_default(user_id)
        profile = dict(stats.get("vocab_profile") or {})
        pack_mastery = self._pack_mastery_map(stats)
        pack_mastery[pack_id] = {
            "stored_pct": round(stored_pct, 4),
            "last_decay_date": last_decay_date,
        }
        profile["pack_mastery"] = pack_mastery
        await self._update(user_id, vocab_profile=profile)
        return pack_mastery[pack_id]

    async def add_pack_mastery_delta(
        self,
        user_id: str,
        pack_id: str,
        delta_pct: float,
        *,
        today_key: str,
    ) -> float:
        from aiforen.domain.vocab_mastery_score import apply_calendar_decay

        pack_id = (pack_id or "").strip()
        if not pack_id or delta_pct <= 0:
            return 0.0

        row = await self.get_pack_mastery(user_id, pack_id)
        stored, decay_date = apply_calendar_decay(
            float(row["stored_pct"]),
            row.get("last_decay_date"),
            today_key,
        )
        stored = max(0.0, stored + float(delta_pct))
        await self.set_pack_mastery(
            user_id, pack_id, stored_pct=stored, last_decay_date=decay_date
        )
        return stored

    def _activity_counts_for_prefix(
        self, daily_activity: Dict[str, Any], prefix: str
    ) -> Dict[str, int]:
        if prefix == "vocab":
            return vocab_counts_from_daily_activity(daily_activity)
        counts: Dict[str, int] = {}
        for day, payload in (daily_activity or {}).items():
            day_key = str(day).strip()[:10]
            if len(day_key) < 10:
                continue
            bump = 0
            if isinstance(payload, dict):
                bump = int(payload.get("grammar") or 0)
            elif isinstance(payload, (int, float)):
                bump = int(payload)
            if bump > 0:
                counts[day_key] = max(counts.get(day_key, 0), bump)
        return counts

    async def _sync_daily_streak(self, user_id: str, *, prefix: str) -> None:
        stats = await self.get_or_default(user_id)
        today = datetime.now(VN_TZ).date()
        counts = self._activity_counts_for_prefix(
            stats.get("daily_activity") or {}, prefix
        )
        streak = compute_daily_streak(counts, today=today)
        best_field = f"{prefix}_best_streak"
        current_field = f"{prefix}_current_streak"
        best = max(int(stats.get(best_field, 0)), streak)
        await self._update(
            user_id,
            **{
                current_field: streak,
                best_field: best,
            },
        )

    async def bump(
        self, user_id: str, *, content_type: str, is_correct: bool, time_taken: int
    ) -> None:
        prefix = "grammar" if content_type == "grammar" else "vocab"
        day_key = datetime.now(VN_TZ).date().isoformat()
        stats = await self.get_or_default(user_id)
        daily = copy.deepcopy(stats.get("daily_activity") or {})
        day_bucket = dict(daily.get(day_key) or {})
        day_bucket[prefix] = int(day_bucket.get(prefix) or 0) + 1
        if prefix == "vocab" and not is_correct:
            day_bucket["vocab_wrong"] = int(day_bucket.get("vocab_wrong") or 0) + 1
        daily[day_key] = day_bucket
        await self._update(
            user_id,
            **{
                f"{prefix}_total_learned": int(
                    stats.get(f"{prefix}_total_learned") or 0
                )
                + 1,
                "total_study_time": int(stats.get("total_study_time") or 0)
                + time_taken,
                "today_study_time": int(stats.get("today_study_time") or 0)
                + time_taken,
                "daily_activity": daily,
                "last_activity": datetime.utcnow(),
            },
        )
        await self._sync_daily_streak(user_id, prefix=prefix)
