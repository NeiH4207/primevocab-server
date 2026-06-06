"""Coaching reading content catalog (CEFR level + day).

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coaching_reading_units",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("cefr_level", sa.String(8), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("topic_slug", sa.String(64), nullable=False),
        sa.Column("topic_title", sa.String(256), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column(
            "paragraphs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "estimated_minutes", sa.Integer(), nullable=False, server_default="8"
        ),
        sa.Column("source_label", sa.String(128), nullable=False),
        sa.Column("question_limit", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
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
        "ix_coaching_reading_unit_lookup",
        "coaching_reading_units",
        ["cefr_level", "day_number", "status"],
    )
    op.create_index(
        "uq_coaching_reading_unit_published",
        "coaching_reading_units",
        ["cefr_level", "day_number"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "coaching_reading_unit_questions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(64),
            sa.ForeignKey("coaching_reading_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("correct_option", sa.Text(), nullable=False),
        sa.Column("acceptable_answers", postgresql.JSONB(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("source_word", sa.String(64), nullable=True),
        sa.UniqueConstraint(
            "unit_id", "sort_order", name="uq_coaching_reading_question_order"
        ),
    )
    op.create_index(
        "ix_coaching_reading_question_unit",
        "coaching_reading_unit_questions",
        ["unit_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coaching_reading_question_unit",
        table_name="coaching_reading_unit_questions",
    )
    op.drop_table("coaching_reading_unit_questions")
    op.drop_index(
        "uq_coaching_reading_unit_published",
        table_name="coaching_reading_units",
    )
    op.drop_index(
        "ix_coaching_reading_unit_lookup",
        table_name="coaching_reading_units",
    )
    op.drop_table("coaching_reading_units")
