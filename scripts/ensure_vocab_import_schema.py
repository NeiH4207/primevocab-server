#!/usr/bin/env python3
"""Minimal Postgres DDL so vocab_storage bulk import matches runtime models.

Idempotent IF NOT EXISTS — not Alembic. Use before import_vocab_storage_bulk.
"""

from __future__ import annotations

import os
import sys

import psycopg2

DDL = [
    # Legacy DBs used column "type"; runtime uses task_type.
    """
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vocab_questions' AND column_name = 'type'
      ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vocab_questions' AND column_name = 'task_type'
      ) THEN
        ALTER TABLE vocab_questions RENAME COLUMN type TO task_type;
      END IF;
    END $$
    """,
    """
    ALTER TABLE vocab_questions
      ADD COLUMN IF NOT EXISTS track_id VARCHAR(32) NOT NULL DEFAULT 'cefr:B1',
      ADD COLUMN IF NOT EXISTS skill VARCHAR(32) NOT NULL DEFAULT 'meaning',
      ADD COLUMN IF NOT EXISTS level_code VARCHAR(16) NOT NULL DEFAULT 'B1',
      ADD COLUMN IF NOT EXISTS mastery_slot INTEGER,
      ADD COLUMN IF NOT EXISTS interaction_kind VARCHAR(16) NOT NULL DEFAULT 'mcq',
      ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb
    """,
    """
    ALTER TABLE vocab_questions
      ALTER COLUMN task_type TYPE VARCHAR(64)
    """,
    "ALTER TABLE vocab_questions DROP CONSTRAINT IF EXISTS ck_vocab_question_type",
    """
    ALTER TABLE vocab_questions
      ADD COLUMN IF NOT EXISTS quality_score INTEGER,
      ADD COLUMN IF NOT EXISTS quality_tier VARCHAR(16),
      ADD COLUMN IF NOT EXISTS quality_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
      ADD COLUMN IF NOT EXISTS content_revision INTEGER NOT NULL DEFAULT 1,
      ADD COLUMN IF NOT EXISTS sense_content_hash VARCHAR(64)
    """,
]


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL (postgresql://, not +asyncpg)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def main() -> None:
    conn = psycopg2.connect(_pg_url())
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in DDL:
        cur.execute(stmt)
    cur.close()
    conn.close()
    print("vocab_questions import schema ready", file=sys.stderr)


if __name__ == "__main__":
    main()
