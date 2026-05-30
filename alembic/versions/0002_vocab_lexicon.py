"""Vocabulary lexicon tables in Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vocab_lexemes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("lemma", sa.String(128), nullable=False),
        sa.Column("display_word", sa.String(128), nullable=False),
        sa.Column("pos", sa.String(32), nullable=False),
        sa.Column("cefr_level", sa.String(8), nullable=True),
        sa.Column("ielts_band_min", sa.Numeric(3, 1), nullable=True),
        sa.Column("ielts_band_max", sa.Numeric(3, 1), nullable=True),
        sa.Column("gre_tier", sa.String(16), nullable=True),
        sa.Column("frequency_rank", sa.Integer(), nullable=True),
        sa.Column("difficulty_score", sa.Numeric(6, 3), nullable=True),
        sa.Column(
            "is_academic", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_ielts_relevant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "exam_types",
            postgresql.ARRAY(sa.String(16)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("sources", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("lemma", "pos", name="uq_vocab_lexeme_lemma_pos"),
        sa.CheckConstraint(
            "status in ('draft','enriched','approved','deprecated')",
            name="ck_vocab_lexeme_status",
        ),
    )
    op.create_index(
        "ix_vocab_lexeme_band", "vocab_lexemes", ["ielts_band_min", "ielts_band_max"]
    )
    op.create_index("ix_vocab_lexeme_status", "vocab_lexemes", ["status"])

    op.create_table(
        "vocab_senses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sense_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition_en", sa.Text(), nullable=False),
        sa.Column("vi_gloss", sa.Text(), nullable=True),
        sa.Column("register", sa.String(32), nullable=True),
        sa.Column(
            "topic_tags",
            postgresql.ARRAY(sa.String(64)),
            server_default=sa.text("'{}'"),
        ),
        sa.Column("ielts_example", sa.Text(), nullable=True),
        sa.Column("gre_example", sa.Text(), nullable=True),
        sa.Column("common_mistake", sa.Text(), nullable=True),
        sa.Column("vi_translate_prompt", sa.Text(), nullable=True),
        sa.Column("topic_prompt", sa.Text(), nullable=True),
        sa.Column("usage_note", sa.Text(), nullable=True),
        sa.Column("tips", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("lexeme_id", "sense_order", name="uq_vocab_sense_order"),
    )
    op.create_index("ix_vocab_sense_lexeme", "vocab_senses", ["lexeme_id"])

    op.create_table(
        "vocab_word_forms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("form", sa.String(128), nullable=False),
        sa.Column("pos", sa.String(32), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
    )
    op.create_index("ix_vocab_word_form_lexeme", "vocab_word_forms", ["lexeme_id"])

    op.create_table(
        "vocab_collocations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phrase", sa.String(255), nullable=False),
        sa.Column("pattern", sa.String(128), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("band_min", sa.Numeric(3, 1), nullable=True),
        sa.Column(
            "is_core", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index("ix_vocab_collocation_lexeme", "vocab_collocations", ["lexeme_id"])

    op.create_table(
        "vocab_packs",
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default="General"),
        sa.Column("task_type", sa.String(32), nullable=False, server_default="Both"),
        sa.Column("exam_type", sa.String(16), nullable=False, server_default="ielts"),
        sa.Column("skill_focus", sa.String(64), nullable=True),
        sa.Column("topic", sa.String(64), nullable=True),
        sa.Column(
            "source_band_min", sa.Numeric(3, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "source_band_max", sa.Numeric(3, 1), nullable=False, server_default="9"
        ),
        sa.Column(
            "target_band_min", sa.Numeric(3, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "target_band_max", sa.Numeric(3, 1), nullable=False, server_default="9"
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_premium", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_vocab_pack_sort", "vocab_packs", ["sort_order"])

    op.create_table(
        "vocab_pack_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pack_id",
            sa.String(64),
            sa.ForeignKey("vocab_packs.pack_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sense_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_senses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_core", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.UniqueConstraint("pack_id", "lexeme_id", name="uq_vocab_pack_item"),
    )
    op.create_index(
        "ix_vocab_pack_item_order", "vocab_pack_items", ["pack_id", "order_index"]
    )

    op.create_table(
        "vocab_questions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sense_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_senses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("correct_option_id", sa.String(8), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", sa.String(16), nullable=False, server_default="generated"),
        sa.Column(
            "generator_meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type in ('meaning_mcq','cloze','collocation','usage_fix','paraphrase','gre_completion')",
            name="ck_vocab_question_type",
        ),
        sa.CheckConstraint(
            "status in ('generated','validated','approved','rejected')",
            name="ck_vocab_question_status",
        ),
    )
    op.create_index(
        "ix_vocab_question_lexeme", "vocab_questions", ["lexeme_id", "type", "status"]
    )

    op.create_table(
        "vocab_review_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_vocab_review_priority", "vocab_review_queue", ["priority", "resolved_at"]
    )

    op.create_table(
        "vocab_legacy_word_map",
        sa.Column("legacy_word_id", sa.String(128), primary_key=True),
        sa.Column(
            "lexeme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vocab_lexemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("vocab_legacy_word_map")
    op.drop_table("vocab_review_queue")
    op.drop_table("vocab_questions")
    op.drop_table("vocab_pack_items")
    op.drop_table("vocab_packs")
    op.drop_table("vocab_collocations")
    op.drop_table("vocab_word_forms")
    op.drop_table("vocab_senses")
    op.drop_table("vocab_lexemes")
