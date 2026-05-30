"""Add phonetic + audio_url to vocab_senses.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vocab_senses", sa.Column("phonetic", sa.String(64), nullable=True))
    op.add_column("vocab_senses", sa.Column("audio_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vocab_senses", "audio_url")
    op.drop_column("vocab_senses", "phonetic")
