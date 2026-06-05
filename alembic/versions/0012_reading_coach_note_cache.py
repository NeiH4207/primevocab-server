"""Reading Coach helper-note cache table.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reading_coach_note_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("reading_id", sa.String(128), nullable=False),
        sa.Column("selection_type", sa.String(16), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("sentence_text", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("user_level", sa.String(8), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column(
            "card_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
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
        "ix_reading_coach_cache_lookup",
        "reading_coach_note_cache",
        ["reading_id", "selection_type", "locale", "user_level"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reading_coach_cache_lookup", table_name="reading_coach_note_cache"
    )
    op.drop_table("reading_coach_note_cache")
