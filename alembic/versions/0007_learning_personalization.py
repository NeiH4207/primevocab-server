"""Learning personalization tracking tables.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "source_content_type",
            sa.String(32),
            nullable=False,
            server_default="vocabulary",
        ),
        sa.Column(
            "content_type", sa.String(32), nullable=False, server_default="vocabulary"
        ),
        sa.Column("word_id", sa.String(128), nullable=True),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pack_id", sa.String(64), nullable=True),
        sa.Column("question_type", sa.String(64), nullable=True),
        sa.Column("step", sa.String(64), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Numeric(6, 3), nullable=True),
        sa.Column("time_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "answer_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ai_eval_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "weakness_tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("event_id", name="uq_learning_event_id"),
    )
    op.create_index(
        "ix_learning_events_user_time", "learning_events", ["user_id", "occurred_at"]
    )
    op.create_index(
        "ix_learning_events_user_content",
        "learning_events",
        ["user_id", "content_type", "word_id"],
    )
    op.create_index(
        "ix_learning_events_pack_time", "learning_events", ["pack_id", "occurred_at"]
    )

    op.create_table(
        "vocab_user_word_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("word_id", sa.String(128), nullable=False),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pack_id", sa.String(64), nullable=True),
        sa.Column("mastery_level", sa.String(32), nullable=False, server_default="new"),
        sa.Column("mastery_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "mastery_point_pct", sa.Numeric(8, 4), nullable=False, server_default="0"
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "marked_known",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "best_translate_pct", sa.Numeric(8, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "best_topic_pct", sa.Numeric(8, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "last_result",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "weakness_tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("first_studied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_studied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "word_id", name="uq_vocab_user_word_state"),
    )
    op.create_index(
        "ix_vocab_word_state_user_due", "vocab_user_word_state", ["user_id", "due_at"]
    )
    op.create_index(
        "ix_vocab_word_state_user_pack", "vocab_user_word_state", ["user_id", "pack_id"]
    )

    op.create_table(
        "vocab_user_pack_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_id", sa.String(64), nullable=False),
        sa.Column("mastery_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("learned_words", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mastered_words", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("focus_band", sa.Numeric(3, 1), nullable=True),
        sa.Column(
            "active_band_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_decay_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_studied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "pack_id", name="uq_vocab_user_pack_state"),
    )
    op.create_index(
        "ix_vocab_pack_state_user_band",
        "vocab_user_pack_state",
        ["user_id", "focus_band"],
    )

    op.create_table(
        "user_learning_daily_rollups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("focus_band", sa.Numeric(3, 1), nullable=True),
        sa.Column(
            "action_counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "weak_dimensions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "pack_counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "category_counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_time_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "day", name="uq_user_learning_daily_rollup"),
    )
    op.create_index(
        "ix_user_learning_rollup_user_day",
        "user_learning_daily_rollups",
        ["user_id", "day"],
    )

    op.create_table(
        "user_learning_weaknesses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("severity", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "recommended_action_type",
            sa.String(64),
            nullable=False,
            server_default="repair_weakness",
        ),
        sa.Column("pack_id", sa.String(64), nullable=True),
        sa.Column("stat_label", sa.String(64), nullable=True),
        sa.Column("band", sa.Numeric(3, 1), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "dimension", "label", name="uq_user_learning_weakness"
        ),
    )
    op.create_index(
        "ix_user_learning_weakness_user_severity",
        "user_learning_weaknesses",
        ["user_id", "severity"],
    )

    op.create_table(
        "vocab_daily_missions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mission_date", sa.Date(), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "output",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("model_provider", sa.String(32), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="generated"),
        sa.Column("refresh_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "error_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "mission_date", "locale", name="uq_vocab_daily_mission"
        ),
    )
    op.create_index(
        "ix_vocab_daily_mission_user_date",
        "vocab_daily_missions",
        ["user_id", "mission_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_vocab_daily_mission_user_date", table_name="vocab_daily_missions")
    op.drop_table("vocab_daily_missions")
    op.drop_index(
        "ix_user_learning_weakness_user_severity", table_name="user_learning_weaknesses"
    )
    op.drop_table("user_learning_weaknesses")
    op.drop_index(
        "ix_user_learning_rollup_user_day", table_name="user_learning_daily_rollups"
    )
    op.drop_table("user_learning_daily_rollups")
    op.drop_index("ix_vocab_pack_state_user_band", table_name="vocab_user_pack_state")
    op.drop_table("vocab_user_pack_state")
    op.drop_index("ix_vocab_word_state_user_pack", table_name="vocab_user_word_state")
    op.drop_index("ix_vocab_word_state_user_due", table_name="vocab_user_word_state")
    op.drop_table("vocab_user_word_state")
    op.drop_index("ix_learning_events_pack_time", table_name="learning_events")
    op.drop_index("ix_learning_events_user_content", table_name="learning_events")
    op.drop_index("ix_learning_events_user_time", table_name="learning_events")
    op.drop_table("learning_events")
