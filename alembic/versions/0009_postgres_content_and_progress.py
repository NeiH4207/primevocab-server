"""Postgres tables for writing, grammar, learner stats (Mongo replacement).

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vocab_user_word_state",
        sa.Column(
            "progress_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "writing_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "writing_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("writing_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_name", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("data_description", sa.Text(), nullable=True),
        sa.Column("time_limit", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column(
            "difficulty", sa.String(32), nullable=False, server_default="intermediate"
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(64)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "access",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("tests_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "average_score", sa.Numeric(4, 2), nullable=False, server_default="0"
        ),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("is_personal", sa.Boolean(), nullable=False, server_default="false"),
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
    )
    op.create_index("ix_writing_task_group", "writing_tasks", ["group_id"])
    op.create_index(
        "ix_writing_task_personal", "writing_tasks", ["created_by", "is_personal"]
    )

    op.create_table(
        "writing_submissions",
        sa.Column("submission_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("writing_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column(
            "task_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("assessment", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_writing_sub_user_created",
        "writing_submissions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_writing_sub_status_finished",
        "writing_submissions",
        ["status", "finished_at"],
    )
    op.create_index("ix_writing_sub_task", "writing_submissions", ["task_id"])

    op.create_table(
        "grammar_structures",
        sa.Column("structure_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("structure_pattern", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False, server_default="Both"),
        sa.Column("band_score", sa.Numeric(3, 1), nullable=False, server_default="6.0"),
        sa.Column(
            "difficulty_level",
            sa.String(32),
            nullable=False,
            server_default="intermediate",
        ),
        sa.Column(
            "examples",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "common_errors",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("total_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    )
    op.create_index("ix_grammar_structure_category", "grammar_structures", ["category"])

    op.create_table(
        "grammar_learning_progress",
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
        sa.Column("structure_id", sa.String(64), nullable=False),
        sa.Column("mastery_level", sa.String(32), nullable=False, server_default="new"),
        sa.Column(
            "progress_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_studied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "structure_id", name="uq_grammar_learning_progress"
        ),
    )
    op.create_index(
        "ix_grammar_progress_user",
        "grammar_learning_progress",
        ["user_id", "last_studied_at"],
    )

    op.create_table(
        "user_learning_stats",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "grammar_total_learned", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("grammar_mastered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "grammar_accuracy",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "grammar_current_streak", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "grammar_best_streak", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "vocab_total_learned", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("vocab_mastered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "vocab_accuracy", sa.Numeric(5, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "vocab_current_streak", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "vocab_best_streak", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_study_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("today_study_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "estimated_grammar_band",
            sa.Numeric(3, 1),
            nullable=False,
            server_default="5.0",
        ),
        sa.Column(
            "estimated_vocab_band",
            sa.Numeric(3, 1),
            nullable=False,
            server_default="5.0",
        ),
        sa.Column(
            "vocab_profile",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "daily_activity",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_activity",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "vocab_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("word_id", sa.String(128), nullable=False),
        sa.Column("pack_id", sa.String(64), nullable=True),
        sa.Column("attempt_type", sa.String(64), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("answer", postgresql.JSONB(), nullable=True),
        sa.Column("ai_feedback", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_vocab_attempt_user_created",
        "vocab_attempts",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("vocab_attempts")
    op.drop_table("user_learning_stats")
    op.drop_table("grammar_learning_progress")
    op.drop_table("grammar_structures")
    op.drop_table("writing_submissions")
    op.drop_table("writing_tasks")
    op.drop_table("writing_groups")
    op.drop_column("vocab_user_word_state", "progress_data")
