"""Reset vocab coaching reading snapshots and progress for all users.

Clears legacy Cambridge / stale reading JSON, workspace reading state, and
rebuilds each day from the Postgres catalog (or placeholder) via
``_ensure_day_content``.

Run:
  python -m aiforen.scripts.vocab.reset_coaching_reading
  python -m aiforen.scripts.vocab.reset_coaching_reading --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, Tuple

from loguru import logger
from sqlalchemy import select

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabCoachingDay, VocabCoachingPlan
from aiforen.services.vocab_coaching_service import VocabCoachingService

_READING_WORKSPACE_DEFAULTS: Dict[str, Any] = {
    "reading_page_index": 0,
    "reading_passage_done": False,
    "reading_question_index": 0,
    "reading_answers": {},
    "highlights": [],
    "bolds": [],
    "ai_questions": [],
    "reading_vocab_signals": [],
    "focus_plan": None,
}


def _reset_day_reading_state(day: VocabCoachingDay) -> Tuple[str, str]:
    """Return (old_title, new_title preview placeholder)."""
    old_title = str((day.reading or {}).get("title") or "")

    day.reading = {}

    sessions = dict(day.sessions or {})
    workspace = (
        dict(sessions.get("workspace") or {})
        if isinstance(sessions.get("workspace"), dict)
        else {}
    )
    for key, value in _READING_WORKSPACE_DEFAULTS.items():
        workspace[key] = value
    sessions["workspace"] = workspace
    sessions["reading"] = {"status": "pending"}
    day.sessions = sessions

    analysis = dict(day.analysis or {})
    analysis.pop("reading_coach_feed", None)
    day.analysis = analysis

    return old_title, ""


async def reset_all_coaching_reading(*, dry_run: bool = False) -> Dict[str, int]:
    core_db.init_pg()
    stats = {
        "days": 0,
        "migrated_from_catalog": 0,
        "placeholder": 0,
        "unchanged_title": 0,
    }

    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(VocabCoachingDay, VocabCoachingPlan)
                .join(
                    VocabCoachingPlan, VocabCoachingDay.plan_id == VocabCoachingPlan.id
                )
                .order_by(VocabCoachingPlan.cefr_level, VocabCoachingDay.day_number)
            )
        ).all()

        svc = VocabCoachingService(session)
        for day, plan in rows:
            old_title, _ = _reset_day_reading_state(day)
            await svc._ensure_day_content(plan, day)
            new_title = str((day.reading or {}).get("title") or "")
            stats["days"] += 1
            if day.reading.get("placeholder"):
                stats["placeholder"] += 1
            elif day.reading.get("content_unit_id"):
                stats["migrated_from_catalog"] += 1
            if old_title and old_title == new_title:
                stats["unchanged_title"] += 1
            logger.info(
                "reset day plan={} user={} day={} {} -> {} ({})",
                plan.id,
                day.user_id,
                day.day_number,
                old_title or "(empty)",
                new_title or "(empty)",
                plan.cefr_level,
            )

        if dry_run:
            await session.rollback()
            logger.warning("Dry run — rolled back {} day(s)", stats["days"])
        else:
            await session.commit()
            logger.info("Committed reading reset for {} day(s)", stats["days"])

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reset coaching reading for all users")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply changes in memory then roll back (no DB writes)",
    )
    args = parser.parse_args()
    stats = await reset_all_coaching_reading(dry_run=args.dry_run)
    logger.info("Reset summary: {}", stats)


if __name__ == "__main__":
    asyncio.run(main())
