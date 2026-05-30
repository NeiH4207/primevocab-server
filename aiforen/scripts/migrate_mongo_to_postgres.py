"""One-shot migration from MongoDB to Postgres.

Usage:
  MONGO_URL=mongodb://... python -m aiforen.scripts.migrate_mongo_to_postgres

Production volume is tiny (~few docs); re-seeding writing/grammar is usually enough.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aiforen.core import db as core_db
from aiforen.domain.sql_models import (
    GrammarLearningProgress,
    UserLearningStats,
    VocabAttempt,
    VocabUserWordState,
    WritingGroup,
    WritingSubmission,
    WritingTask,
)
from aiforen.repositories.pg.progress_adapters import progress_to_word_state_values


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _migrate_collection(mongo_db, session) -> None:
    # writing groups
    for doc in await mongo_db["writing_groups"].find({}).to_list(None):
        await session.execute(
            pg_insert(WritingGroup)
            .values(
                id=int(doc["id"]),
                name=doc["name"],
                description=doc.get("description"),
                icon=doc.get("icon"),
                sort_order=int(doc.get("sort_order") or 0),
                total_tasks=int(doc.get("total_tasks") or 0),
                is_active=bool(doc.get("is_active", True)),
            )
            .on_conflict_do_update(
                index_elements=[WritingGroup.id],
                set_={
                    "name": doc["name"],
                    "description": doc.get("description"),
                    "icon": doc.get("icon"),
                    "sort_order": int(doc.get("sort_order") or 0),
                    "total_tasks": int(doc.get("total_tasks") or 0),
                    "is_active": bool(doc.get("is_active", True)),
                },
            )
        )

    for doc in await mongo_db["writing_tasks"].find({}).to_list(None):
        await session.execute(
            pg_insert(WritingTask)
            .values(
                id=int(doc["id"]),
                group_id=int(doc["group_id"]),
                group_name=doc["group_name"],
                task_type=doc["task_type"],
                title=doc["title"],
                description=doc.get("description") or "",
                image_url=doc.get("image_url"),
                data_description=doc.get("data_description"),
                time_limit=int(doc.get("time_limit") or 1200),
                difficulty=doc.get("difficulty") or "intermediate",
                tags=list(doc.get("tags") or []),
                access=dict(doc.get("access") or {}),
                tests_taken=int(doc.get("tests_taken") or 0),
                average_score=float(doc.get("average_score") or 0),
                created_by=doc.get("created_by"),
                is_personal=bool(doc.get("is_personal")),
                created_at=doc.get("created_at") or datetime.utcnow(),
                updated_at=doc.get("updated_at") or datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[WritingTask.id],
                set_={
                    "group_id": int(doc["group_id"]),
                    "group_name": doc["group_name"],
                    "task_type": doc["task_type"],
                    "title": doc["title"],
                    "description": doc.get("description") or "",
                    "updated_at": datetime.utcnow(),
                },
            )
        )

    for doc in await mongo_db["writing_submissions"].find({}).to_list(None):
        await session.execute(
            pg_insert(WritingSubmission)
            .values(
                submission_id=doc["submission_id"],
                user_id=_uuid(doc["user_id"]),
                task_id=int(doc["task_id"]),
                answer=doc["answer"],
                word_count=int(doc.get("word_count") or 0),
                status=doc.get("status") or "queued",
                task_snapshot=dict(doc.get("task_snapshot") or {}),
                assessment=doc.get("assessment"),
                error_message=doc.get("error_message"),
                prompt_version=doc.get("prompt_version"),
                started_at=doc.get("started_at"),
                finished_at=doc.get("finished_at"),
                created_at=doc.get("created_at") or datetime.utcnow(),
                updated_at=doc.get("updated_at") or datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[WritingSubmission.submission_id],
                set_={
                    "status": doc.get("status") or "queued",
                    "assessment": doc.get("assessment"),
                    "finished_at": doc.get("finished_at"),
                    "updated_at": datetime.utcnow(),
                },
            )
        )

    for doc in await mongo_db["user_stats"].find({}).to_list(None):
        await session.execute(
            pg_insert(UserLearningStats)
            .values(
                user_id=_uuid(doc["user_id"]),
                grammar_total_learned=int(doc.get("grammar_total_learned") or 0),
                grammar_mastered=int(doc.get("grammar_mastered") or 0),
                grammar_accuracy=float(doc.get("grammar_accuracy") or 0),
                grammar_current_streak=int(doc.get("grammar_current_streak") or 0),
                grammar_best_streak=int(doc.get("grammar_best_streak") or 0),
                vocab_total_learned=int(doc.get("vocab_total_learned") or 0),
                vocab_mastered=int(doc.get("vocab_mastered") or 0),
                vocab_accuracy=float(doc.get("vocab_accuracy") or 0),
                vocab_current_streak=int(doc.get("vocab_current_streak") or 0),
                vocab_best_streak=int(doc.get("vocab_best_streak") or 0),
                total_study_time=int(doc.get("total_study_time") or 0),
                today_study_time=int(doc.get("today_study_time") or 0),
                estimated_grammar_band=float(doc.get("estimated_grammar_band") or 5),
                estimated_vocab_band=float(doc.get("estimated_vocab_band") or 5),
                vocab_profile=dict(doc.get("vocab_profile") or {}),
                daily_activity=dict(doc.get("daily_activity") or {}),
                last_activity=doc.get("last_activity") or datetime.utcnow(),
                created_at=doc.get("created_at") or datetime.utcnow(),
                updated_at=doc.get("updated_at") or datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[UserLearningStats.user_id],
                set_={
                    "vocab_profile": dict(doc.get("vocab_profile") or {}),
                    "daily_activity": dict(doc.get("daily_activity") or {}),
                    "last_activity": doc.get("last_activity") or datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
            )
        )

    for doc in await mongo_db["learning_progress"].find({}).to_list(None):
        content_type = doc.get("content_type")
        if content_type == "grammar":
            await session.execute(
                pg_insert(GrammarLearningProgress)
                .values(
                    user_id=_uuid(doc["user_id"]),
                    structure_id=str(doc["content_id"]),
                    mastery_level=doc.get("mastery_level") or "new",
                    progress_data=dict(doc),
                    last_studied_at=doc.get("last_studied"),
                    updated_at=doc.get("updated_at") or datetime.utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        GrammarLearningProgress.user_id,
                        GrammarLearningProgress.structure_id,
                    ],
                    set_={
                        "mastery_level": doc.get("mastery_level") or "new",
                        "progress_data": dict(doc),
                        "last_studied_at": doc.get("last_studied"),
                        "updated_at": datetime.utcnow(),
                    },
                )
            )
        elif content_type == "vocabulary":
            word_id = str(doc["content_id"])
            row = progress_to_word_state_values(doc["user_id"], word_id, doc)
            await session.execute(
                pg_insert(VocabUserWordState)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=[
                        VocabUserWordState.user_id,
                        VocabUserWordState.word_id,
                    ],
                    set_={
                        "progress_data": row["progress_data"],
                        "mastery_level": row["mastery_level"],
                        "mastery_step": row["mastery_step"],
                        "updated_at": datetime.utcnow(),
                    },
                )
            )

    for doc in await mongo_db["vocab_attempts"].find({}).to_list(None):
        await session.execute(
            pg_insert(VocabAttempt)
            .values(
                attempt_id=doc["attempt_id"],
                user_id=_uuid(doc["user_id"]),
                word_id=doc["word_id"],
                pack_id=doc.get("pack_id"),
                attempt_type=doc.get("attempt_type") or "unknown",
                is_correct=doc.get("is_correct"),
                answer=doc.get("answer"),
                ai_feedback=doc.get("ai_feedback"),
                created_at=doc.get("created_at") or datetime.utcnow(),
            )
            .on_conflict_do_nothing()
        )


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL") or os.environ.get("MONGO_PUBLIC_URL")
    if not mongo_url:
        logger.error("Set MONGO_URL or MONGO_PUBLIC_URL to run migration")
        return

    mongo_db_name = os.environ.get("MONGO_DB", "aiforen")
    client = AsyncIOMotorClient(mongo_url, uuidRepresentation="standard")
    mongo_db = client[mongo_db_name]

    core_db.init_pg()
    async with core_db.pg_session() as session:
        await _migrate_collection(mongo_db, session)
    client.close()
    await core_db.shutdown_all()
    logger.info("Mongo → Postgres migration complete")


if __name__ == "__main__":
    asyncio.run(main())
