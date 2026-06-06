"""Idempotent Postgres schema repairs for production drift."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _table_columns(conn, table: str) -> set[str]:
    rows = (
        await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table
                """
            ),
            {"table": table},
        )
    ).fetchall()
    return {row[0] for row in rows}


async def apply_pg_schema_repairs(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'vocab_questions'
                )
                """
            )
        )
        if not exists:
            logger.info(
                "schema_repair: vocab_questions missing; applying content/progress repairs only"
            )
            await _repair_postgres_content_and_progress(conn)
            return

        cols = await _table_columns(conn, "vocab_questions")

        if "question_type" in cols and "task_type" not in cols:
            await conn.execute(
                text(
                    "ALTER TABLE vocab_questions "
                    "RENAME COLUMN question_type TO task_type"
                )
            )
            cols.remove("question_type")
            cols.add("task_type")
            logger.info("schema_repair: renamed question_type -> task_type")

        if "type" in cols and "task_type" not in cols:
            await conn.execute(
                text("ALTER TABLE vocab_questions RENAME COLUMN type TO task_type")
            )
            cols.remove("type")
            cols.add("task_type")
            logger.info("schema_repair: renamed type -> task_type")

        if "difficulty" not in cols:
            await conn.execute(
                text(
                    "ALTER TABLE vocab_questions "
                    "ADD COLUMN difficulty INTEGER NOT NULL DEFAULT 3"
                )
            )
            cols.add("difficulty")
            logger.info("schema_repair: added vocab_questions.difficulty")

        for col, ddl in (
            ("status", "VARCHAR(16) NOT NULL DEFAULT 'generated'"),
            ("options", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
            ("correct_option_id", "VARCHAR(8) NOT NULL DEFAULT 'A'"),
        ):
            if col not in cols:
                await conn.execute(
                    text(f"ALTER TABLE vocab_questions ADD COLUMN {col} {ddl}")
                )
                logger.info("schema_repair: added vocab_questions.{}", col)

        await _repair_postgres_content_and_progress(conn)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = :table
                )
                """
            ),
            {"table": table},
        )
    )


async def _repair_postgres_content_and_progress(conn) -> None:
    cols = await _table_columns(conn, "vocab_user_word_state")
    if "progress_data" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE vocab_user_word_state "
                "ADD COLUMN progress_data JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        )
        logger.info("schema_repair: added vocab_user_word_state.progress_data")

    if not await _table_exists(conn, "writing_groups"):
        await conn.execute(
            text(
                """
                CREATE TABLE writing_groups (
                  id INTEGER PRIMARY KEY,
                  name VARCHAR(255) NOT NULL,
                  description TEXT,
                  icon VARCHAR(64),
                  sort_order INTEGER NOT NULL DEFAULT 0,
                  total_tasks INTEGER NOT NULL DEFAULT 0,
                  is_active BOOLEAN NOT NULL DEFAULT true
                )
                """
            )
        )
        logger.info("schema_repair: created writing_groups")

    if not await _table_exists(conn, "writing_tasks"):
        await conn.execute(
            text(
                """
                CREATE TABLE writing_tasks (
                  id INTEGER PRIMARY KEY,
                  group_id INTEGER NOT NULL REFERENCES writing_groups(id) ON DELETE CASCADE,
                  group_name VARCHAR(255) NOT NULL,
                  task_type VARCHAR(32) NOT NULL,
                  title VARCHAR(512) NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  image_url TEXT,
                  data_description TEXT,
                  time_limit INTEGER NOT NULL DEFAULT 1200,
                  difficulty VARCHAR(32) NOT NULL DEFAULT 'intermediate',
                  tags VARCHAR(64)[] NOT NULL DEFAULT '{}',
                  access JSONB NOT NULL DEFAULT '{}'::jsonb,
                  tests_taken INTEGER NOT NULL DEFAULT 0,
                  average_score NUMERIC(4,2) NOT NULL DEFAULT 0,
                  created_by VARCHAR(128),
                  is_personal BOOLEAN NOT NULL DEFAULT false,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text("CREATE INDEX ix_writing_task_group ON writing_tasks (group_id)")
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_writing_task_personal ON writing_tasks (created_by, is_personal)"
            )
        )
        logger.info("schema_repair: created writing_tasks")

    if not await _table_exists(conn, "writing_submissions"):
        await conn.execute(
            text(
                """
                CREATE TABLE writing_submissions (
                  submission_id VARCHAR(64) PRIMARY KEY,
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  task_id INTEGER NOT NULL REFERENCES writing_tasks(id) ON DELETE CASCADE,
                  answer TEXT NOT NULL,
                  word_count INTEGER NOT NULL DEFAULT 0,
                  status VARCHAR(32) NOT NULL DEFAULT 'queued',
                  task_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                  assessment JSONB,
                  error_message TEXT,
                  prompt_version VARCHAR(64),
                  started_at TIMESTAMPTZ,
                  finished_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_writing_sub_user_created ON writing_submissions (user_id, created_at)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_writing_sub_status_finished ON writing_submissions (status, finished_at)"
            )
        )
        await conn.execute(
            text("CREATE INDEX ix_writing_sub_task ON writing_submissions (task_id)")
        )
        logger.info("schema_repair: created writing_submissions")

    if not await _table_exists(conn, "grammar_structures"):
        await conn.execute(
            text(
                """
                CREATE TABLE grammar_structures (
                  structure_id VARCHAR(64) PRIMARY KEY,
                  name VARCHAR(255) NOT NULL,
                  structure_pattern TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  category VARCHAR(64) NOT NULL,
                  task_type VARCHAR(32) NOT NULL DEFAULT 'Both',
                  band_score NUMERIC(3,1) NOT NULL DEFAULT 6.0,
                  difficulty_level VARCHAR(32) NOT NULL DEFAULT 'intermediate',
                  examples JSONB NOT NULL DEFAULT '[]'::jsonb,
                  common_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
                  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                  total_attempts INTEGER NOT NULL DEFAULT 0,
                  success_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
                  is_active BOOLEAN NOT NULL DEFAULT true,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_grammar_structure_category ON grammar_structures (category)"
            )
        )
        logger.info("schema_repair: created grammar_structures")

    if not await _table_exists(conn, "grammar_learning_progress"):
        await conn.execute(
            text(
                """
                CREATE TABLE grammar_learning_progress (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  structure_id VARCHAR(64) NOT NULL,
                  mastery_level VARCHAR(32) NOT NULL DEFAULT 'new',
                  progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                  last_studied_at TIMESTAMPTZ,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  UNIQUE (user_id, structure_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_grammar_progress_user ON grammar_learning_progress (user_id, last_studied_at)"
            )
        )
        logger.info("schema_repair: created grammar_learning_progress")

    if not await _table_exists(conn, "user_learning_stats"):
        await conn.execute(
            text(
                """
                CREATE TABLE user_learning_stats (
                  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                  grammar_total_learned INTEGER NOT NULL DEFAULT 0,
                  grammar_mastered INTEGER NOT NULL DEFAULT 0,
                  grammar_accuracy NUMERIC(5,4) NOT NULL DEFAULT 0,
                  grammar_current_streak INTEGER NOT NULL DEFAULT 0,
                  grammar_best_streak INTEGER NOT NULL DEFAULT 0,
                  vocab_total_learned INTEGER NOT NULL DEFAULT 0,
                  vocab_mastered INTEGER NOT NULL DEFAULT 0,
                  vocab_accuracy NUMERIC(5,4) NOT NULL DEFAULT 0,
                  vocab_current_streak INTEGER NOT NULL DEFAULT 0,
                  vocab_best_streak INTEGER NOT NULL DEFAULT 0,
                  total_study_time INTEGER NOT NULL DEFAULT 0,
                  today_study_time INTEGER NOT NULL DEFAULT 0,
                  estimated_grammar_band NUMERIC(3,1) NOT NULL DEFAULT 5.0,
                  estimated_vocab_band NUMERIC(3,1) NOT NULL DEFAULT 5.0,
                  vocab_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                  daily_activity JSONB NOT NULL DEFAULT '{}'::jsonb,
                  last_activity TIMESTAMPTZ NOT NULL DEFAULT now(),
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        logger.info("schema_repair: created user_learning_stats")

    if not await _table_exists(conn, "vocab_attempts"):
        await conn.execute(
            text(
                """
                CREATE TABLE vocab_attempts (
                  attempt_id VARCHAR(64) PRIMARY KEY,
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  word_id VARCHAR(128) NOT NULL,
                  pack_id VARCHAR(64),
                  attempt_type VARCHAR(64) NOT NULL,
                  is_correct BOOLEAN,
                  answer JSONB,
                  ai_feedback JSONB,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_vocab_attempt_user_created ON vocab_attempts (user_id, created_at)"
            )
        )
        logger.info("schema_repair: created vocab_attempts")

    await _repair_vocab_coaching(conn)


async def _repair_vocab_coaching(conn) -> None:
    """Create 31-day vocab coaching tables if a running API is ahead of migrations."""
    if not await _table_exists(conn, "vocab_coaching_plans"):
        await conn.execute(
            text(
                """
                CREATE TABLE vocab_coaching_plans (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  status VARCHAR(16) NOT NULL DEFAULT 'active',
                  cefr_level VARCHAR(8) NOT NULL DEFAULT 'B1',
                  estimated_band NUMERIC(3,1),
                  confidence NUMERIC(5,2),
                  source VARCHAR(16) NOT NULL DEFAULT 'api',
                  start_date DATE NOT NULL,
                  current_day INTEGER NOT NULL DEFAULT 1,
                  total_days INTEGER NOT NULL DEFAULT 31,
                  meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_vocab_coaching_plan_active "
                "ON vocab_coaching_plans (user_id) WHERE status = 'active'"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_vocab_coaching_plan_user "
                "ON vocab_coaching_plans (user_id, status)"
            )
        )
        logger.info("schema_repair: created vocab_coaching_plans")

    if not await _table_exists(conn, "vocab_coaching_days"):
        await conn.execute(
            text(
                """
                CREATE TABLE vocab_coaching_days (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  plan_id UUID NOT NULL REFERENCES vocab_coaching_plans(id) ON DELETE CASCADE,
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  day_number INTEGER NOT NULL,
                  status VARCHAR(16) NOT NULL DEFAULT 'locked',
                  date_key DATE,
                  title TEXT,
                  focus_skill VARCHAR(32),
                  words JSONB NOT NULL DEFAULT '[]'::jsonb,
                  reading JSONB NOT NULL DEFAULT '{}'::jsonb,
                  sessions JSONB NOT NULL DEFAULT '{}'::jsonb,
                  analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
                  notes JSONB NOT NULL DEFAULT '[]'::jsonb,
                  started_at TIMESTAMPTZ,
                  completed_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  UNIQUE (plan_id, day_number)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_vocab_coaching_day_plan "
                "ON vocab_coaching_days (plan_id, day_number)"
            )
        )
        logger.info("schema_repair: created vocab_coaching_days")

    if not await _table_exists(conn, "vocab_coaching_events"):
        await conn.execute(
            text(
                """
                CREATE TABLE vocab_coaching_events (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  plan_id UUID NOT NULL REFERENCES vocab_coaching_plans(id) ON DELETE CASCADE,
                  day_id UUID REFERENCES vocab_coaching_days(id) ON DELETE CASCADE,
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  day_number INTEGER NOT NULL,
                  event_type VARCHAR(32) NOT NULL,
                  word VARCHAR(128),
                  phrase TEXT,
                  sentence TEXT,
                  is_correct BOOLEAN,
                  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_vocab_coaching_event_day "
                "ON vocab_coaching_events (plan_id, day_number, event_type)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_vocab_coaching_event_user "
                "ON vocab_coaching_events (user_id, created_at)"
            )
        )
        logger.info("schema_repair: created vocab_coaching_events")

    if not await _table_exists(conn, "reading_coach_note_cache"):
        await conn.execute(
            text(
                """
                CREATE TABLE reading_coach_note_cache (
                  cache_key VARCHAR(64) PRIMARY KEY,
                  reading_id VARCHAR(128) NOT NULL,
                  selection_type VARCHAR(16) NOT NULL,
                  target_text TEXT NOT NULL,
                  sentence_text TEXT NOT NULL,
                  locale VARCHAR(8) NOT NULL,
                  user_level VARCHAR(8) NOT NULL,
                  prompt_version VARCHAR(16) NOT NULL,
                  model_name VARCHAR(128) NOT NULL,
                  card_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                  hit_count INTEGER NOT NULL DEFAULT 0,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_reading_coach_cache_lookup "
                "ON reading_coach_note_cache (reading_id, selection_type, locale, user_level)"
            )
        )
        logger.info("schema_repair: created reading_coach_note_cache")

    if not await _table_exists(conn, "coaching_reading_units"):
        await conn.execute(
            text(
                """
                CREATE TABLE coaching_reading_units (
                  id VARCHAR(64) PRIMARY KEY,
                  cefr_level VARCHAR(8) NOT NULL,
                  day_number INTEGER NOT NULL,
                  topic_slug VARCHAR(64) NOT NULL,
                  topic_title VARCHAR(256) NOT NULL,
                  title VARCHAR(256) NOT NULL,
                  paragraphs JSONB NOT NULL DEFAULT '[]'::jsonb,
                  estimated_minutes INTEGER NOT NULL DEFAULT 8,
                  source_label VARCHAR(128) NOT NULL,
                  question_limit INTEGER NOT NULL DEFAULT 7,
                  content_version INTEGER NOT NULL DEFAULT 1,
                  status VARCHAR(16) NOT NULL DEFAULT 'draft',
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_coaching_reading_unit_lookup "
                "ON coaching_reading_units (cefr_level, day_number, status)"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_coaching_reading_unit_published "
                "ON coaching_reading_units (cefr_level, day_number) "
                "WHERE status = 'published'"
            )
        )
        logger.info("schema_repair: created coaching_reading_units")

    if await _table_exists(conn, "coaching_reading_units"):
        unit_cols = await _table_columns(conn, "coaching_reading_units")
        if "vocab_keywords" not in unit_cols:
            await conn.execute(
                text(
                    "ALTER TABLE coaching_reading_units "
                    "ADD COLUMN vocab_keywords JSONB NOT NULL DEFAULT '[]'::jsonb"
                )
            )
            logger.info("schema_repair: added coaching_reading_units.vocab_keywords")

    if not await _table_exists(conn, "coaching_reading_unit_questions"):
        await conn.execute(
            text(
                """
                CREATE TABLE coaching_reading_unit_questions (
                  id VARCHAR(64) PRIMARY KEY,
                  unit_id VARCHAR(64) NOT NULL
                    REFERENCES coaching_reading_units(id) ON DELETE CASCADE,
                  sort_order INTEGER NOT NULL,
                  question_type VARCHAR(32) NOT NULL,
                  prompt TEXT NOT NULL,
                  options JSONB,
                  correct_option TEXT NOT NULL,
                  acceptable_answers JSONB,
                  explanation TEXT,
                  source_word VARCHAR(64),
                  UNIQUE (unit_id, sort_order)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_coaching_reading_question_unit "
                "ON coaching_reading_unit_questions (unit_id, sort_order)"
            )
        )
        logger.info("schema_repair: created coaching_reading_unit_questions")
