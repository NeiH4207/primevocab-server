"""Expand Postgres ck_vocab_question_task_type for new vocab_storage templates.

  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.sync_quiz_task_type_constraint
"""

from __future__ import annotations

import os
import re

import psycopg2
from loguru import logger

from aiforen.scripts.vocab.quiz_import_utils import QUIZ_ALLOWED_TASK_TYPES

CONSTRAINT_NAME = "ck_vocab_question_task_type"


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL (postgresql://, not +asyncpg)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _task_types_from_constraint(defn: str) -> set[str]:
    return set(re.findall(r"'([^']+)'", defn or ""))


def sync_task_type_constraint(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname = %s
        """,
        (CONSTRAINT_NAME,),
    )
    row = cur.fetchone()
    existing: set[str] = set()
    if row and row[0]:
        existing = _task_types_from_constraint(row[0])

    merged = sorted(existing | set(QUIZ_ALLOWED_TASK_TYPES))
    added = sorted(set(QUIZ_ALLOWED_TASK_TYPES) - existing)
    if not added and row:
        logger.info(
            "Task type constraint already includes catalog ({} types)", len(merged)
        )
        cur.close()
        return

    literals = ", ".join(f"'{name.replace(chr(39), '')}'" for name in merged)
    cur.execute(
        f"ALTER TABLE vocab_questions DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
    )
    cur.execute(
        f"ALTER TABLE vocab_questions ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"CHECK (task_type IN ({literals}))"
    )
    conn.commit()
    cur.close()
    logger.info(
        "Updated {}: {} total task types (added: {})",
        CONSTRAINT_NAME,
        len(merged),
        ", ".join(added) if added else "none",
    )


def main() -> None:
    conn = psycopg2.connect(_pg_url())
    try:
        sync_task_type_constraint(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
