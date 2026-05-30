"""Repair vocab_questions columns on DBs created before full lexicon migration.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {col["name"] for col in insp.get_columns(table)}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("vocab_questions"):
        return

    cols = _column_names("vocab_questions")

    if "type" not in cols:
        op.add_column(
            "vocab_questions",
            sa.Column(
                "type",
                sa.String(32),
                nullable=False,
                server_default="meaning_mcq",
            ),
        )
        op.alter_column("vocab_questions", "type", server_default=None)

    cols = _column_names("vocab_questions")
    if "status" not in cols:
        op.add_column(
            "vocab_questions",
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default="generated",
            ),
        )
        op.alter_column("vocab_questions", "status", server_default=None)

    cols = _column_names("vocab_questions")
    if "options" not in cols:
        op.add_column(
            "vocab_questions",
            sa.Column(
                "options",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    cols = _column_names("vocab_questions")
    if "correct_option_id" not in cols:
        op.add_column(
            "vocab_questions",
            sa.Column(
                "correct_option_id",
                sa.String(8),
                nullable=False,
                server_default="A",
            ),
        )
        op.alter_column("vocab_questions", "correct_option_id", server_default=None)

    bind = op.get_bind()
    insp = sa.inspect(bind)
    index_names = {idx["name"] for idx in insp.get_indexes("vocab_questions")}
    if "ix_vocab_question_lexeme" not in index_names and "type" in _column_names(
        "vocab_questions"
    ):
        op.create_index(
            "ix_vocab_question_lexeme",
            "vocab_questions",
            ["lexeme_id", "type", "status"],
            unique=False,
        )


def downgrade() -> None:
    # Non-destructive repair migration; no downgrade.
    pass
