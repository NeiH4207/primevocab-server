"""Ensure coaching reading target lemmas exist in Postgres with quiz-ready questions.

Reads target_vocabulary from vocab_storage/coaching_reading/*.json (A1–C1 by default),
upserts missing lexemes + primary senses, and inserts a minimal meaning_mcq when no
vocab_storage question exists (required for coaching focus seeds).

Run:
  VOCAB_STORAGE_DIR=../vocab_storage python -m aiforen.scripts.vocab.ensure_coaching_reading_lemmas
  python -m aiforen.scripts.vocab.ensure_coaching_reading_lemmas --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aiforen.core import db as core_db
from aiforen.domain.coaching_reading_v2 import storage_reading_dir
from aiforen.domain.sql_models import VocabLexeme, VocabQuestion, VocabSense
from aiforen.repositories.pg.vocab_lexicon import (
    VOCAB_STORAGE_SOURCE,
    VocabLexiconRepo,
    lexeme_id_for,
)
from aiforen.scripts.vocab.oxford_packs import CEFR_IELTS_BAND, normalize_pos
from aiforen.scripts.vocab.quiz_import_utils import track_id_from_level

DEFAULT_LEVELS = ("A1", "A2", "B1", "B2", "C1")
_USABLE_LEXEME_STATUS = frozenset({"enriched", "approved"})
_USABLE_QUESTION_STATUS = frozenset({"validated", "approved", "generated"})


def collect_target_rows(levels: set[str]) -> Dict[str, Dict[str, Any]]:
    """Unique lemma -> best metadata row from coaching reading JSON."""
    rows: Dict[str, Dict[str, Any]] = {}
    for path in sorted(storage_reading_dir().glob("*.json")):
        unit = json.loads(path.read_text(encoding="utf-8"))
        lvl = str(unit.get("cefr_level") or "").upper()
        if lvl not in levels:
            continue
        for item in unit.get("target_vocabulary") or []:
            lemma = str(item.get("lemma") or "").strip()
            if not lemma:
                continue
            key = lemma.lower()
            pos = normalize_pos(str(item.get("pos") or "noun"))
            level = str(item.get("level") or lvl).upper()
            vi_gloss = str(item.get("vi_gloss") or "").strip() or None
            simple = str(item.get("simple_meaning") or "").strip()
            if key not in rows:
                rows[key] = {
                    "lemma": lemma,
                    "display_word": lemma,
                    "pos": pos,
                    "level": level,
                    "vi_gloss": vi_gloss,
                    "definition_en": simple or lemma,
                }
                continue
            existing = rows[key]
            if vi_gloss and not existing.get("vi_gloss"):
                existing["vi_gloss"] = vi_gloss
            if simple and existing.get("definition_en") == existing["lemma"]:
                existing["definition_en"] = simple
    return rows


def _ielts_band(level: str) -> Tuple[Optional[float], Optional[float]]:
    return CEFR_IELTS_BAND.get((level or "B1").upper(), (5.0, 6.5))


def _stub_options(
    lemma: str, vi_gloss: Optional[str]
) -> Tuple[List[Dict[str, str]], str]:
    gloss = (vi_gloss or lemma).strip()
    return (
        [
            {"id": "a", "text": gloss},
            {"id": "b", "text": "khác"},
            {"id": "c", "text": "trái nghĩa"},
            {"id": "d", "text": "không liên quan"},
        ],
        "a",
    )


def _question_id_for(lexeme_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(lexeme_id, "coaching_reading.stub.meaning_mcq")


async def _primary_sense(
    repo: VocabLexiconRepo, lexeme_id: uuid.UUID
) -> Optional[VocabSense]:
    return (
        await repo.s.execute(
            select(VocabSense)
            .where(VocabSense.lexeme_id == lexeme_id)
            .order_by(VocabSense.sense_order)
            .limit(1)
        )
    ).scalar_one_or_none()


_REJECTED_QUESTION_STATUS = frozenset({"rejected", "archived"})


def _tag_question_meta(question: VocabQuestion) -> bool:
    meta = dict(question.generator_meta or {})
    if meta.get("source") == VOCAB_STORAGE_SOURCE:
        return False
    meta["source"] = VOCAB_STORAGE_SOURCE
    meta.setdefault("storage_file", "coaching_reading")
    meta["coaching_reading_tagged"] = True
    question.generator_meta = meta
    if question.status in _REJECTED_QUESTION_STATUS:
        question.status = "validated"
    return True


async def _first_usable_question(
    repo: VocabLexiconRepo, lexeme_id: uuid.UUID
) -> Optional[VocabQuestion]:
    return (
        await repo.s.execute(
            select(VocabQuestion)
            .where(
                VocabQuestion.lexeme_id == lexeme_id,
                VocabQuestion.status.in_(tuple(_USABLE_QUESTION_STATUS)),
            )
            .order_by(VocabQuestion.mastery_slot.asc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _slot_question(
    repo: VocabLexiconRepo,
    *,
    lexeme_id: uuid.UUID,
    track_id: str,
    task_type: str = "meaning_mcq",
    mastery_slot: int = 1,
) -> Optional[VocabQuestion]:
    return (
        await repo.s.execute(
            select(VocabQuestion)
            .where(
                VocabQuestion.lexeme_id == lexeme_id,
                VocabQuestion.track_id == track_id,
                VocabQuestion.type == task_type,
                VocabQuestion.mastery_slot == mastery_slot,
                VocabQuestion.status.notin_(tuple(_REJECTED_QUESTION_STATUS)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _has_vocab_storage_question(
    repo: VocabLexiconRepo, lexeme_id: uuid.UUID
) -> bool:
    row = (
        (
            await repo.s.execute(
                select(VocabQuestion.id)
                .where(
                    VocabQuestion.lexeme_id == lexeme_id,
                    VocabQuestion.status.in_(tuple(_USABLE_QUESTION_STATUS)),
                )
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if not row:
        return False
    questions = (
        (await repo.s.execute(select(VocabQuestion).where(VocabQuestion.id.in_(row))))
        .scalars()
        .all()
    )
    return any(
        (q.generator_meta or {}).get("source") == VOCAB_STORAGE_SOURCE
        for q in questions
    )


async def _resolve_lexeme(
    repo: VocabLexiconRepo, lemma: str, pos: str
) -> Optional[VocabLexeme]:
    preferred_id = lexeme_id_for(lemma.lower(), pos)
    rows = list(
        (
            await repo.s.execute(
                select(VocabLexeme)
                .where(func.lower(VocabLexeme.lemma) == lemma.lower())
                .order_by(VocabLexeme.frequency_rank.asc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    quiz_ready: List[VocabLexeme] = []
    for lexeme in rows:
        if await _first_usable_question(repo, lexeme.id):
            quiz_ready.append(lexeme)
    if quiz_ready:
        for lexeme in quiz_ready:
            if lexeme.id == preferred_id:
                return lexeme
        return quiz_ready[0]

    preferred = next((row for row in rows if row.id == preferred_id), None)
    return preferred or rows[0]


async def ensure_one(
    repo: VocabLexiconRepo,
    row: Dict[str, Any],
    *,
    dry_run: bool,
) -> Tuple[str, str]:
    lemma = row["lemma"]
    pos = row["pos"]
    level = row["level"]
    lexeme_id_for(lemma.lower(), pos)
    lexeme = await _resolve_lexeme(repo, lemma, pos)

    created_lexeme = False
    created_sense = False
    created_question = False

    if lexeme is None:
        if dry_run:
            return "would_create_lexeme", lemma
        bmin, bmax = _ielts_band(level)
        lexeme = await repo.upsert_lexeme(
            lemma=lemma.lower(),
            pos=pos,
            display_word=row.get("display_word") or lemma,
            cefr_level=level,
            ielts_band_min=bmin,
            ielts_band_max=bmax,
            exam_types=["ielts"],
            sources=[{"source": "coaching_reading", "kind": "target_vocabulary"}],
            status="approved",
        )
        created_lexeme = True
    elif lexeme.status not in _USABLE_LEXEME_STATUS:
        if not dry_run:
            lexeme.status = "approved"
            await repo.s.flush()

    sense = await _primary_sense(repo, lexeme.id)
    if sense is None:
        if dry_run:
            return "would_create_sense", lemma
        await repo.upsert_primary_sense(
            lexeme.id,
            definition_en=row.get("definition_en") or lemma,
            vi_gloss=row.get("vi_gloss"),
            topic_tags=[lemma.lower()],
            tips=[],
        )
        created_sense = True
    elif row.get("vi_gloss") and not sense.vi_gloss and not dry_run:
        sense.vi_gloss = row["vi_gloss"]
        await repo.s.flush()

    if await _has_vocab_storage_question(repo, lexeme.id):
        if created_lexeme or created_sense:
            return "lexeme_or_sense_only", lemma
        return "ok", lemma

    existing_q = await _first_usable_question(repo, lexeme.id)
    if existing_q is None:
        track_id = track_id_from_level(level)
        existing_q = await _slot_question(
            repo,
            lexeme_id=lexeme.id,
            track_id=track_id,
        )
    if existing_q is not None:
        if dry_run:
            return "would_tag_question", lemma
        if _tag_question_meta(existing_q):
            await repo.s.flush()
            return "tagged_question", lemma
        return "ok", lemma

    if dry_run:
        return "would_create_question", lemma

    sense = await _primary_sense(repo, lexeme.id)
    options, correct = _stub_options(lemma, row.get("vi_gloss"))
    prompt = f'What is the meaning of "{lemma}"?'
    track_id = track_id_from_level(level)
    qid = _question_id_for(lexeme.id)
    existing_q = await repo.get_question(qid)
    if existing_q is None:
        try:
            async with repo.s.begin_nested():
                question = VocabQuestion(
                    id=qid,
                    lexeme_id=lexeme.id,
                    sense_id=sense.id if sense else None,
                    track_id=track_id,
                    type="meaning_mcq",
                    skill="meaning",
                    level_code=level,
                    mastery_slot=1,
                    interaction_kind="mcq",
                    prompt=prompt,
                    options=options,
                    correct_option_id=correct,
                    explanation=row.get("vi_gloss")
                    or row.get("definition_en")
                    or lemma,
                    payload={},
                    difficulty=2,
                    status="validated",
                    generator_meta={
                        "source": VOCAB_STORAGE_SOURCE,
                        "storage_file": "coaching_reading",
                        "coaching_reading_stub": True,
                    },
                    quality_issues=[],
                )
                repo.s.add(question)
                await repo.s.flush()
            created_question = True
        except IntegrityError:
            existing_q = await _slot_question(
                repo, lexeme_id=lexeme.id, track_id=track_id
            )
            if existing_q is not None and _tag_question_meta(existing_q):
                await repo.s.flush()
                return "tagged_question", lemma
            raise

    if created_lexeme:
        return "created_lexeme", lemma
    if created_sense:
        return "created_sense", lemma
    if created_question:
        return "created_question", lemma
    return "ok", lemma


async def ensure_coaching_reading_lemmas(
    *,
    levels: set[str],
    dry_run: bool = False,
) -> Dict[str, int]:
    rows = collect_target_rows(levels)
    stats: Dict[str, int] = {
        "total": len(rows),
        "ok": 0,
        "created_lexeme": 0,
        "created_sense": 0,
        "created_question": 0,
        "lexeme_or_sense_only": 0,
        "would_create_lexeme": 0,
        "would_create_sense": 0,
        "would_create_question": 0,
    }

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    batch = 0
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        for row in rows.values():
            try:
                action, lemma = await ensure_one(repo, row, dry_run=dry_run)
                stats[action] = stats.get(action, 0) + 1
                if action.startswith("created") or action.startswith("would_create"):
                    logger.info("{} — {}", action, lemma)
                batch += 1
                if not dry_run and batch % 50 == 0:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                stats["errors"] = stats.get("errors", 0) + 1
                logger.error("ensure failed for {}: {}", row.get("lemma"), exc)
        if not dry_run:
            await session.commit()
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure coaching reading lemmas are quiz-ready in Postgres"
    )
    parser.add_argument(
        "--levels",
        default=",".join(DEFAULT_LEVELS),
        help="Comma-separated CEFR levels (default: A1-C1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report actions without writing",
    )
    args = parser.parse_args()
    levels = {x.strip().upper() for x in args.levels.split(",") if x.strip()}
    stats = await ensure_coaching_reading_lemmas(levels=levels, dry_run=args.dry_run)
    logger.info("ensure_coaching_reading_lemmas stats: {}", stats)


if __name__ == "__main__":
    asyncio.run(main())
