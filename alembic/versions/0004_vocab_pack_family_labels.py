"""pack_family on packs; stat_labels on pack items (analytics only).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vocab_packs",
        sa.Column("pack_family", sa.String(16), nullable=False, server_default="band"),
    )
    op.add_column(
        "vocab_pack_items",
        sa.Column(
            "stat_labels",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index("ix_vocab_pack_family", "vocab_packs", ["pack_family", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_vocab_pack_family", table_name="vocab_packs")
    op.drop_column("vocab_pack_items", "stat_labels")
    op.drop_column("vocab_packs", "pack_family")
