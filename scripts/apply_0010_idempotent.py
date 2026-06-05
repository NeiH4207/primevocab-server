#!/usr/bin/env python3
"""Apply 0010_vocab_coaching_workouts schema idempotently (IF NOT EXISTS).

Does NOT modify alembic_version. Safe when prod is stamped at 0017 but missing 0010 objects.
"""
from __future__ import annotations

import os
import sys

import psycopg2

DDL_STATEMENTS = [
    # vocab_questions quality columns (may already exist on prod)
    """
    ALTER TABLE vocab_questions
      ADD COLUMN IF NOT EXISTS quality_score INTEGER,
      ADD COLUMN IF NOT EXISTS quality_tier VARCHAR(16),
      ADD COLUMN IF NOT EXISTS quality_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
      ADD COLUMN IF NOT EXISTS content_revision INTEGER NOT NULL DEFAULT 1,
      ADD COLUMN IF NOT EXISTS sense_content_hash VARCHAR(64)
    """,
    """
    UPDATE vocab_questions
    SET quality_score = COALESCE(quality_score, 70),
        quality_tier = COALESCE(quality_tier, 'good')
    WHERE status IN ('validated', 'approved')
      AND (quality_tier IS NULL OR quality_score IS NULL)
    """,
    # learning_events workout columns
    """
    ALTER TABLE learning_events
      ADD COLUMN IF NOT EXISTS workout_id UUID,
      ADD COLUMN IF NOT EXISTS workout_item_id UUID,
      ADD COLUMN IF NOT EXISTS skill_id VARCHAR(32),
      ADD COLUMN IF NOT EXISTS mastery_slot INTEGER,
      ADD COLUMN IF NOT EXISTS interaction_kind VARCHAR(16)
    """,
    # user_learning_weaknesses
    """
    ALTER TABLE user_learning_weaknesses
      ADD COLUMN IF NOT EXISTS success_streak INTEGER NOT NULL DEFAULT 0
    """,
    # vocab_coaching_workouts
    """
    CREATE TABLE IF NOT EXISTS vocab_coaching_workouts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        workout_date DATE NOT NULL,
        track_id VARCHAR(32) NOT NULL DEFAULT 'cefr:B1',
        cefr_level VARCHAR(8) NOT NULL DEFAULT 'B1',
        status VARCHAR(24) NOT NULL DEFAULT 'ready',
        focus_skill VARCHAR(32) NOT NULL DEFAULT 'meaning',
        intensity VARCHAR(16) NOT NULL DEFAULT 'standard',
        estimated_minutes INTEGER NOT NULL DEFAULT 10,
        coach_copy JSONB NOT NULL DEFAULT '{}'::jsonb,
        progress JSONB NOT NULL DEFAULT '{}'::jsonb,
        summary JSONB NOT NULL DEFAULT '{}'::jsonb,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_vocab_coaching_workout_day UNIQUE (user_id, workout_date, track_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_vocab_coaching_workout_user_date
      ON vocab_coaching_workouts (user_id, workout_date)
    """,
    # vocab_coaching_workout_items
    """
    CREATE TABLE IF NOT EXISTS vocab_coaching_workout_items (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        workout_id UUID NOT NULL REFERENCES vocab_coaching_workouts(id) ON DELETE CASCADE,
        phase VARCHAR(16) NOT NULL,
        order_index INTEGER NOT NULL,
        word_id VARCHAR(128) NOT NULL,
        question_id UUID NOT NULL REFERENCES vocab_questions(id) ON DELETE CASCADE,
        mastery_slot INTEGER NOT NULL,
        skill_id VARCHAR(32) NOT NULL,
        interaction_kind VARCHAR(16) NOT NULL,
        is_required BOOLEAN NOT NULL DEFAULT true,
        status VARCHAR(24) NOT NULL DEFAULT 'pending',
        attempt_id VARCHAR(128),
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        repair_parent_id UUID REFERENCES vocab_coaching_workout_items(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_vocab_coaching_workout_item_order UNIQUE (workout_id, order_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_vocab_coaching_item_workout_order
      ON vocab_coaching_workout_items (workout_id, order_index)
    """,
    # vocab_user_skill_state
    """
    CREATE TABLE IF NOT EXISTS vocab_user_skill_state (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        track_id VARCHAR(32) NOT NULL,
        skill_id VARCHAR(32) NOT NULL,
        score NUMERIC(6, 3) NOT NULL DEFAULT 0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        correct_count INTEGER NOT NULL DEFAULT 0,
        incorrect_count INTEGER NOT NULL DEFAULT 0,
        success_streak INTEGER NOT NULL DEFAULT 0,
        due_at TIMESTAMPTZ,
        last_seen_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_vocab_user_skill_state UNIQUE (user_id, track_id, skill_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_vocab_user_skill_state_user_due
      ON vocab_user_skill_state (user_id, due_at)
    """,
]

VERIFY_QUERIES = [
    ("alembic_version", "SELECT version_num FROM alembic_version"),
    (
        "tables",
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN (
            'vocab_coaching_workouts',
            'vocab_coaching_workout_items',
            'vocab_user_skill_state'
          )
        ORDER BY 1
        """,
    ),
    (
        "learning_events_cols",
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'learning_events'
          AND column_name IN (
            'workout_id', 'workout_item_id', 'skill_id', 'mastery_slot', 'interaction_kind'
          )
        ORDER BY 1
        """,
    ),
    (
        "weakness_success_streak",
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'user_learning_weaknesses' AND column_name = 'success_streak'
        """,
    ),
    (
        "quality_tier_backfill",
        """
        SELECT
          COUNT(*) FILTER (WHERE status IN ('validated','approved')) AS approved,
          COUNT(*) FILTER (WHERE status IN ('validated','approved') AND quality_tier IS NOT NULL) AS with_tier
        FROM vocab_questions
        """,
    ),
]


def main() -> int:
    url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        print("Set DATABASE_URL or DATABASE_PUBLIC_URL", file=sys.stderr)
        return 1

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for i, stmt in enumerate(DDL_STATEMENTS, 1):
                label = stmt.strip().split("\n")[0][:72]
                print(f"[{i}/{len(DDL_STATEMENTS)}] {label}...")
                cur.execute(stmt)
        conn.commit()
        print("DDL committed.")

        with conn.cursor() as cur:
            for name, q in VERIFY_QUERIES:
                cur.execute(q)
                rows = cur.fetchall()
                print(f"\n--- {name} ---")
                for row in rows:
                    print(" ", row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
