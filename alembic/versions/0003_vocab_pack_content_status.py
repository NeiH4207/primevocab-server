"""Add pack content workflow columns.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vocab_packs",
        sa.Column(
            "content_status", sa.String(32), nullable=False, server_default="draft"
        ),
    )
    op.add_column(
        "vocab_packs",
        sa.Column(
            "target_word_count", sa.Integer(), nullable=False, server_default="12"
        ),
    )
    op.add_column(
        "vocab_packs",
        sa.Column(
            "completed_word_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_vocab_pack_content_status", "vocab_packs", ["content_status"])


def downgrade() -> None:
    op.drop_index("ix_vocab_pack_content_status", table_name="vocab_packs")
    op.drop_column("vocab_packs", "completed_word_count")
    op.drop_column("vocab_packs", "target_word_count")
    op.drop_column("vocab_packs", "content_status")
