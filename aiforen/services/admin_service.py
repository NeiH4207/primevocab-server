"""Admin product metrics (Postgres identity/billing + learner activity)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import User, UserLearningStats, VocabUserWordState

UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


class AdminService:
    def __init__(self, pg: AsyncSession):
        self.pg = pg

    async def verify_admin(self, user_id: str) -> Dict[str, Any]:
        uid = uuid.UUID(user_id)
        user = await self.pg.get(User, uid)
        if not user:
            return {"id": user_id, "email": "", "is_admin": False, "db_verified": False}
        return {
            "id": str(user.id),
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "db_verified": True,
        }

    async def overview(self) -> Dict[str, Any]:
        now = _utc_now()
        as_of = now.isoformat()
        day_start = _day_start(now)
        week_start = now - timedelta(days=7)

        total_users = int(
            await self.pg.scalar(select(func.count()).select_from(User)) or 0
        )
        signups_7d = int(
            await self.pg.scalar(
                select(func.count()).where(User.created_at >= week_start)
            )
            or 0
        )
        from aiforen.domain.sql_models import Subscription

        paid_users = int(
            await self.pg.scalar(
                select(func.count(func.distinct(Subscription.user_id))).where(
                    Subscription.status == "active",
                    Subscription.current_period_end > now,
                )
            )
            or 0
        )

        dau = int(
            await self.pg.scalar(
                select(func.count()).where(UserLearningStats.last_activity >= day_start)
            )
            or 0
        )
        wau = int(
            await self.pg.scalar(
                select(func.count()).where(
                    UserLearningStats.last_activity >= week_start
                )
            )
            or 0
        )
        calibrated_users = int(
            await self.pg.scalar(
                select(func.count()).where(
                    UserLearningStats.vocab_profile["calibration_completed"].astext
                    == "true"
                )
            )
            or 0
        )
        calibration_rate = (
            float(calibrated_users) / total_users if total_users > 0 else 0.0
        )

        avg_vocab_words_learned = float(
            await self.pg.scalar(
                select(func.avg(UserLearningStats.vocab_total_learned))
            )
            or 0
        )

        return {
            "as_of": as_of,
            "total_users": total_users,
            "signups_7d": signups_7d,
            "dau": dau,
            "wau": wau,
            "paid_users": paid_users,
            "calibrated_users": calibrated_users,
            "calibration_rate": round(calibration_rate, 4),
            "avg_vocab_words_learned": round(avg_vocab_words_learned, 2),
        }

    async def funnel(self, *, window_days: int = 30) -> Dict[str, Any]:
        window_days = max(1, min(int(window_days), 365))
        now = _utc_now()
        since = now - timedelta(days=window_days)

        signups = int(
            await self.pg.scalar(select(func.count()).where(User.created_at >= since))
            or 0
        )
        calibrated = int(
            await self.pg.scalar(
                select(func.count()).where(
                    UserLearningStats.vocab_profile["calibration_completed"].astext
                    == "true",
                    UserLearningStats.vocab_profile["calibration_completed_at"].astext
                    >= since.isoformat(),
                )
            )
            or 0
        )
        if calibrated < signups:
            calibrated = int(
                await self.pg.scalar(
                    select(func.count()).where(
                        UserLearningStats.vocab_profile["calibration_completed"].astext
                        == "true",
                        UserLearningStats.updated_at >= since,
                    )
                )
                or 0
            )

        first_vocab_session = int(
            await self.pg.scalar(
                select(func.count(func.distinct(VocabUserWordState.user_id))).where(
                    VocabUserWordState.first_studied_at >= since,
                    cast(
                        VocabUserWordState.progress_data["total_attempts"].astext,
                        Integer,
                    )
                    > 0,
                )
            )
            or 0
        )

        calibration_rate = float(calibrated) / signups if signups > 0 else 0.0
        first_session_rate = (
            float(first_vocab_session) / signups if signups > 0 else 0.0
        )

        return {
            "window_days": window_days,
            "signups": signups,
            "calibrated": calibrated,
            "first_vocab_session": first_vocab_session,
            "calibration_rate": round(calibration_rate, 4),
            "first_session_rate": round(first_session_rate, 4),
        }

    async def retention(self, *, cohort_days: int = 14) -> Dict[str, Any]:
        cohort_days = max(1, min(int(cohort_days), 90))
        today = _utc_now().date()
        cohorts: List[Dict[str, Any]] = []

        for offset in range(cohort_days):
            cohort_date = today - timedelta(days=offset + 1)
            start = datetime.combine(cohort_date, datetime.min.time(), tzinfo=UTC)
            end = start + timedelta(days=1)

            signup_rows = (
                (
                    await self.pg.execute(
                        select(User.id).where(
                            User.created_at >= start,
                            User.created_at < end,
                        )
                    )
                )
                .scalars()
                .all()
            )
            user_ids = [str(uid) for uid in signup_rows]
            cohort_size = len(user_ids)
            if cohort_size == 0:
                cohorts.append(
                    {
                        "cohort_date": cohort_date.isoformat(),
                        "cohort_size": 0,
                        "d1_active": 0,
                        "d7_active": 0,
                        "d1_rate": 0.0,
                        "d7_rate": 0.0,
                    }
                )
                continue

            d1_start = end
            d1_end = d1_start + timedelta(days=1)
            d7_start = end
            d7_end = d7_start + timedelta(days=1)

            d1_active = await self._count_active_users(user_ids, d1_start, d1_end)
            d7_active = await self._count_active_users(user_ids, d7_start, d7_end)

            cohorts.append(
                {
                    "cohort_date": cohort_date.isoformat(),
                    "cohort_size": cohort_size,
                    "d1_active": d1_active,
                    "d7_active": d7_active,
                    "d1_rate": round(d1_active / cohort_size, 4),
                    "d7_rate": round(d7_active / cohort_size, 4),
                }
            )

        return {"cohort_days": cohort_days, "cohorts": cohorts}

    async def _count_active_users(
        self,
        user_ids: List[str],
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        if not user_ids:
            return 0
        uuids = [uuid.UUID(uid) for uid in user_ids]
        return int(
            await self.pg.scalar(
                select(func.count()).where(
                    UserLearningStats.user_id.in_(uuids),
                    UserLearningStats.last_activity >= window_start,
                    UserLearningStats.last_activity < window_end,
                )
            )
            or 0
        )
