"""Upsert coaching reading catalog from vocab_storage JSON (schema v2).

Source of truth: vocab_storage/coaching_reading/*.json

Run:
  python -m aiforen.scripts.vocab.seed_coaching_reading_catalog
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import delete, update

from aiforen.core import db as core_db
from aiforen.domain.coaching_reading_v2 import load_all_v2_upsert_payloads
from aiforen.domain.sql_models import CoachingReadingUnit, CoachingReadingUnitQuestion
from aiforen.repositories.pg.coaching_content import CoachingContentRepo

# Legacy catalog ids — archived before publishing v2 units.
RETIRED_READING_UNIT_IDS = (
    "a2-day01-city-park",
    "a2-day02-vocab-app",
    "a2-day01-vocab-app",
    "b1-day01-study-habit",
    "b2-day01-urban-heat",
    "b2-day02-food-waste",
    "c1-day01-green-gentrification",
)


async def retire_legacy_reading_units(repo: CoachingContentRepo) -> None:
    await repo.s.execute(
        delete(CoachingReadingUnitQuestion).where(
            CoachingReadingUnitQuestion.unit_id.in_(RETIRED_READING_UNIT_IDS)
        )
    )
    await repo.s.execute(
        update(CoachingReadingUnit)
        .where(CoachingReadingUnit.id.in_(RETIRED_READING_UNIT_IDS))
        .values(status="archived")
    )
    logger.info(
        "Archived retired reading units: {}", ", ".join(RETIRED_READING_UNIT_IDS)
    )


async def seed_from_vocab_storage(repo: CoachingContentRepo) -> None:
    """Upsert all units from vocab_storage/coaching_reading/*.json (schema v2)."""
    payloads = load_all_v2_upsert_payloads()
    if not payloads:
        raise RuntimeError(
            "No coaching reading units found in vocab_storage/coaching_reading"
        )
    for payload in payloads:
        await repo.upsert_unit(
            unit_id=payload["unit_id"],
            cefr_level=payload["cefr_level"],
            day_number=payload["day_number"],
            topic_slug=payload["topic_slug"],
            topic_title=payload["topic_title"],
            title=payload["title"],
            paragraphs=payload["paragraphs"],
            source_label=payload["source_label"],
            estimated_minutes=payload["estimated_minutes"],
            question_limit=payload["question_limit"],
            content_version=payload["content_version"],
            status=payload["status"],
            questions=payload["questions"],
            vocab_keywords=payload["vocab_keywords"],
        )
        logger.info("Seeded coaching reading unit {}", payload["unit_id"])


async def main() -> None:
    core_db.init_pg()
    async with core_db.pg_session() as session:
        repo = CoachingContentRepo(session)
        await retire_legacy_reading_units(repo)
        await seed_from_vocab_storage(repo)
    logger.info("Coaching reading catalog seed complete")


if __name__ == "__main__":
    asyncio.run(main())
