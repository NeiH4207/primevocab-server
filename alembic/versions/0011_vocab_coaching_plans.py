"""Adaptive 31-day vocab coaching plans, days, and learner action events.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vocab_coaching_plans",
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
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("cefr_level", sa.String(8), nullable=False, server_default="B1"),
        sa.Column("estimated_band", sa.Numeric(3, 1), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="api"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("current_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_days", sa.Integer(), nullable=False, server_default="31"),
        sa.Column(
            "meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
    op.create_index(
        "uq_vocab_coaching_plan_active",
        "vocab_coaching_plans",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_vocab_coaching_plan_user",
        "vocab_coaching_plans",
        ["user_id", "status"],
    )

    op.create_table(
        "vocab_coaching_days",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_coaching_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="locked"),
        sa.Column("date_key", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("focus_skill", sa.String(32), nullable=True),
        sa.Column(
            "words",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "reading",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "sessions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "analysis",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "notes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
            "plan_id", "day_number", name="uq_vocab_coaching_day_number"
        ),
    )
    op.create_index(
        "ix_vocab_coaching_day_plan",
        "vocab_coaching_days",
        ["plan_id", "day_number"],
    )

    op.create_table(
        "vocab_coaching_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_coaching_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "day_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_coaching_days.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("word", sa.String(128), nullable=True),
        sa.Column("phrase", sa.Text(), nullable=True),
        sa.Column("sentence", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_vocab_coaching_event_day",
        "vocab_coaching_events",
        ["plan_id", "day_number", "event_type"],
    )
    op.create_index(
        "ix_vocab_coaching_event_user",
        "vocab_coaching_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vocab_coaching_event_user", table_name="vocab_coaching_events")
    op.drop_index("ix_vocab_coaching_event_day", table_name="vocab_coaching_events")
    op.drop_table("vocab_coaching_events")
    op.drop_index("ix_vocab_coaching_day_plan", table_name="vocab_coaching_days")
    op.drop_table("vocab_coaching_days")
    op.drop_index("ix_vocab_coaching_plan_user", table_name="vocab_coaching_plans")
    op.drop_index("uq_vocab_coaching_plan_active", table_name="vocab_coaching_plans")
    op.drop_table("vocab_coaching_plans")
