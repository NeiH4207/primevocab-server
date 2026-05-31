"""Adaptive vocab coaching workouts and quiz quality metadata.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vocab_questions", sa.Column("quality_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "vocab_questions", sa.Column("quality_tier", sa.String(16), nullable=True)
    )
    op.add_column(
        "vocab_questions",
        sa.Column(
            "quality_issues",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "vocab_questions",
        sa.Column("content_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "vocab_questions", sa.Column("sense_content_hash", sa.String(64), nullable=True)
    )
    op.execute(
        """
        UPDATE vocab_questions
        SET quality_score = COALESCE(quality_score, 70),
            quality_tier = COALESCE(quality_tier, 'good')
        WHERE status IN ('validated', 'approved')
        """
    )

    op.add_column(
        "learning_events",
        sa.Column(
            "workout_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "learning_events",
        sa.Column(
            "workout_item_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "learning_events", sa.Column("skill_id", sa.String(32), nullable=True)
    )
    op.add_column(
        "learning_events", sa.Column("mastery_slot", sa.Integer(), nullable=True)
    )
    op.add_column(
        "learning_events", sa.Column("interaction_kind", sa.String(16), nullable=True)
    )

    op.add_column(
        "user_learning_weaknesses",
        sa.Column("success_streak", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "vocab_coaching_workouts",
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
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("track_id", sa.String(32), nullable=False, server_default="cefr:B1"),
        sa.Column("cefr_level", sa.String(8), nullable=False, server_default="B1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="ready"),
        sa.Column(
            "focus_skill", sa.String(32), nullable=False, server_default="meaning"
        ),
        sa.Column(
            "intensity", sa.String(16), nullable=False, server_default="standard"
        ),
        sa.Column(
            "estimated_minutes", sa.Integer(), nullable=False, server_default="10"
        ),
        sa.Column(
            "coach_copy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "progress",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "user_id", "workout_date", "track_id", name="uq_vocab_coaching_workout_day"
        ),
    )
    op.create_index(
        "ix_vocab_coaching_workout_user_date",
        "vocab_coaching_workouts",
        ["user_id", "workout_date"],
    )

    op.create_table(
        "vocab_coaching_workout_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workout_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_coaching_workouts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("word_id", sa.String(128), nullable=False),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mastery_slot", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.String(32), nullable=False),
        sa.Column("interaction_kind", sa.String(16), nullable=False),
        sa.Column(
            "is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempt_id", sa.String(128), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "repair_parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_coaching_workout_items.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.UniqueConstraint(
            "workout_id", "order_index", name="uq_vocab_coaching_workout_item_order"
        ),
    )
    op.create_index(
        "ix_vocab_coaching_item_workout_order",
        "vocab_coaching_workout_items",
        ["workout_id", "order_index"],
    )

    op.create_table(
        "vocab_user_skill_state",
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
        sa.Column("track_id", sa.String(32), nullable=False),
        sa.Column("skill_id", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
            "user_id", "track_id", "skill_id", name="uq_vocab_user_skill_state"
        ),
    )
    op.create_index(
        "ix_vocab_user_skill_state_user_due",
        "vocab_user_skill_state",
        ["user_id", "due_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vocab_user_skill_state_user_due", table_name="vocab_user_skill_state"
    )
    op.drop_table("vocab_user_skill_state")
    op.drop_index(
        "ix_vocab_coaching_item_workout_order",
        table_name="vocab_coaching_workout_items",
    )
    op.drop_table("vocab_coaching_workout_items")
    op.drop_index(
        "ix_vocab_coaching_workout_user_date", table_name="vocab_coaching_workouts"
    )
    op.drop_table("vocab_coaching_workouts")
    op.drop_column("user_learning_weaknesses", "success_streak")
    for column in (
        "interaction_kind",
        "mastery_slot",
        "skill_id",
        "workout_item_id",
        "workout_id",
    ):
        op.drop_column("learning_events", column)
    for column in (
        "sense_content_hash",
        "content_revision",
        "quality_issues",
        "quality_tier",
        "quality_score",
    ):
        op.drop_column("vocab_questions", column)
