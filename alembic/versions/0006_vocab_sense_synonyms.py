"""Add synonyms JSONB to vocab_senses.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("vocab_senses")}
    if "synonyms" in columns:
        return
    op.add_column(
        "vocab_senses",
        sa.Column(
            "synonyms",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("vocab_senses")}
    if "synonyms" not in columns:
        return
    op.drop_column("vocab_senses", "synonyms")
