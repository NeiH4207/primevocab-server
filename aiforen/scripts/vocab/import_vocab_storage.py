"""Import curated vocab + quiz content from ../vocab_storage into Postgres.

Data source (repo sibling):
  - vocab_storage/vocab_full_table.json  (~7563 senses)
  - vocab_storage/quiz_*_vocab.json      (~48k MCQ rows; non-MCQ skipped)

Matches lexemes by (lemma, pos) using lexeme_id_for() so existing production rows
are enriched in place. Rebuilds pack_items from pack_id on each vocab row.

Usage:
  python -m aiforen.scripts.vocab.import_vocab_storage
  python -m aiforen.scripts.vocab.import_vocab_storage --dry-run
  python -m aiforen.scripts.vocab.import_vocab_storage --skip-questions
  VOCAB_STORAGE_DIR=/path/to/vocab_storage python -m aiforen.scripts.vocab.import_vocab_storage
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import delete, select

from aiforen.core import db as core_db
from aiforen.domain.sql_models import (
    VocabCollocation,
    VocabLexeme,
    VocabPack,
    VocabSense,
)
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo, lexeme_id_for
from aiforen.scripts.vocab.build_packs import BAND_PACKS, build_thematic_packs
from aiforen.scripts.vocab.oxford_packs import (
    CEFR_IELTS_BAND,
    OXFORD_PACKS,
    normalize_pos,
)
from aiforen.scripts.vocab.pack_specs import infer_stat_labels

DEFAULT_STORAGE = Path(__file__).resolve().parents[4] / "vocab_storage"

QUIZ_TASK_TO_DB = {
    "meaning_in_context": "meaning_mcq",
    "meaning_mcq": "meaning_mcq",
    "collocation_mcq": "collocation",
    "pattern_cloze": "cloze",
    "error_diagnosis": "usage_fix",
    "cloze": "cloze",
    "collocation": "collocation",
    "usage_fix": "usage_fix",
    "paraphrase": "paraphrase",
    "gre_completion": "gre_completion",
    "gre_sentence_completion": "gre_completion",
    # C1 / C2 advanced MCQ (stored as native task_type in PG)
    "nuance_in_context": "nuance_in_context",
    "academic_collocation": "academic_collocation",
    "register_choice": "register_choice",
    "precision_in_context": "precision_in_context",
    "precision_cloze": "precision_cloze",
    "register_tone_judgment": "register_tone_judgment",
    # IELTS track
    "ielts_topic_meaning_mcq": "ielts_topic_meaning_mcq",
    "ielts_collocation_cloze": "ielts_collocation_cloze",
    "ielts_paraphrase_recognition": "ielts_paraphrase_recognition",
    # GRE track
    "gre_precision_definition": "gre_precision_definition",
    "gre_logic_contrast": "gre_logic_contrast",
    "gre_text_completion": "gre_text_completion",
    "gre_sentence_equivalence": "gre_sentence_equivalence",
}

PACK_BAND_META = {p["pack_id"]: p for p in BAND_PACKS}
PACK_OXFORD_META = {p["pack_id"]: p for p in OXFORD_PACKS}

GRE_PACK = {
    "pack_id": "pack_gre",
    "title": "GRE Vocabulary",
    "description": "High-difficulty words for GRE verbal reasoning.",
    "category": "GRE",
    "pack_family": "gre",
    "exam_type": "gre",
    "sort_order": 20,
    "target_band_min": 8.0,
    "target_band_max": 9.0,
}


def _storage_dir() -> Path:
    raw = os.environ.get("VOCAB_STORAGE_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_STORAGE


def _normalize_pos(raw: str) -> str:
    return normalize_pos((raw or "noun").strip().lower())


def _exam_types(row: Dict[str, Any]) -> List[str]:
    family = (row.get("pack_family") or "").lower()
    if family == "gre" or row.get("gre_tier"):
        return ["gre", "ielts"]
    if family == "oxford" or str(row.get("pack_id", "")).startswith("pack_oxford"):
        return ["ielts", "oxford"]
    return ["ielts"]


def _parse_collocations(raw: Any) -> List[Tuple[str, Optional[str]]]:
    if not raw or not isinstance(raw, str):
        return []
    out: List[Tuple[str, Optional[str]]] = []
    for part in raw.split("|"):
        part = part.strip()
        if not part:
            continue
        if "::" in part:
            phrase, example = part.split("::", 1)
            out.append((phrase.strip(), example.strip() or None))
        else:
            out.append((part.strip(), None))
    return out[:12]


def _wire_options(options: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], str]:
    wired: List[Dict[str, str]] = []
    correct = "a"
    for opt in options or []:
        oid = str(opt.get("id") or "").strip().lower() or "a"
        if len(oid) > 1 and oid.isalpha():
            oid = oid[0]
        text = str(opt.get("text") or "").strip()
        wired.append({"id": oid, "text": text})
        if opt.get("is_correct"):
            correct = oid
    if not wired:
        wired = [{"id": "a", "text": ""}]
    return wired, correct


def _question_prompt(q: Dict[str, Any]) -> str:
    prompt = (q.get("prompt") or "").strip()
    context = (q.get("context") or "").strip()
    if context and context not in prompt:
        return f"{prompt}\n\n{context}" if prompt else context
    return prompt or "Choose the best answer."


def _map_question_status(raw: Optional[str]) -> str:
    if raw in ("approved", "validated"):
        return "approved"
    if raw in ("rejected", "archived"):
        return "rejected"
    return "generated"


async def _ensure_packs(repo: VocabLexiconRepo, pack_ids: set[str]) -> None:
    await build_thematic_packs(repo, reset_items=False)
    for spec in OXFORD_PACKS:
        cefr = spec.get("cefr_level") or "B1"
        band = CEFR_IELTS_BAND.get(cefr, (5.0, 7.0))
        await repo.upsert_pack(
            {
                **spec,
                "task_type": "Both",
                "source_band_min": 0.0,
                "source_band_max": 9.0,
                "target_band_min": band[0],
                "target_band_max": band[1],
                "is_active": True,
                "content_status": "filled",
            }
        )
    if "pack_gre" in pack_ids:
        await repo.upsert_pack(
            {
                **GRE_PACK,
                "task_type": "Both",
                "source_band_min": 0.0,
                "source_band_max": 9.0,
                "is_active": True,
                "content_status": "filled",
                "target_word_count": 0,
                "completed_word_count": 0,
            }
        )
    for pack_id in pack_ids:
        if pack_id in PACK_BAND_META:
            spec = PACK_BAND_META[pack_id]
            await repo.upsert_pack(
                {
                    "pack_id": pack_id,
                    "title": spec["title"],
                    "description": spec["description"],
                    "category": spec["category"],
                    "task_type": "Both",
                    "exam_type": spec["exam_type"],
                    "pack_family": spec.get("pack_family", "band"),
                    "source_band_min": 0.0,
                    "source_band_max": 9.0,
                    "target_band_min": spec.get("target_band_min", 0.0),
                    "target_band_max": spec.get("target_band_max", 9.0),
                    "sort_order": spec.get("sort_order", 0),
                    "is_active": True,
                    "is_premium": spec.get("is_premium", False),
                    "content_status": "filled",
                }
            )
        elif pack_id in PACK_OXFORD_META:
            pass  # handled above
        elif pack_id == "pack_gre":
            pass
        else:
            logger.warning("Unknown pack_id in vocab file: {}", pack_id)


async def import_lexemes(
    repo: VocabLexiconRepo,
    rows: List[Dict[str, Any]],
    *,
    dry_run: bool,
    enrich_only: bool = False,
) -> Dict[str, uuid.UUID]:
    """Returns storage sense_id -> DB lexeme_id."""
    sense_to_lexeme: Dict[str, uuid.UUID] = {}
    stats = {"lexemes": 0, "senses": 0, "collocations": 0, "legacy_maps": 0}

    for i, row in enumerate(rows, start=1):
        lemma = (row.get("lemma") or row.get("display_word") or "").strip().lower()
        pos = _normalize_pos(row.get("pos") or "noun")
        if not lemma:
            continue
        lid = lexeme_id_for(lemma, pos)
        sense_to_lexeme[str(row.get("sense_id") or "")] = lid

        status = "approved" if row.get("lex_status") == "approved" else "enriched"
        band_min = row.get("ielts_band_min")
        band_max = row.get("ielts_band_max")
        try:
            bmin = float(band_min) if band_min is not None else None
        except (TypeError, ValueError):
            bmin = None
        try:
            bmax = float(band_max) if band_max is not None else None
        except (TypeError, ValueError):
            bmax = None

        if dry_run:
            stats["lexemes"] += 1
            continue

        if not enrich_only:
            lexeme = await repo.upsert_lexeme(
                lemma=lemma,
                pos=pos,
                display_word=(row.get("display_word") or lemma).strip(),
                cefr_level=row.get("cefr_level"),
                ielts_band_min=bmin,
                ielts_band_max=bmax,
                gre_tier=row.get("gre_tier"),
                is_academic=(bmin or 0) >= 7.0,
                exam_types=_exam_types(row),
                sources=[{"name": "vocab_storage", "readiness": row.get("readiness")}],
                status=status,
            )
            lexeme_id = lexeme.id
        else:
            lexeme_id = lid
            exists = await repo.s.scalar(
                select(VocabLexeme.id).where(VocabLexeme.id == lexeme_id)
            )
            if not exists:
                continue

        patterns = row.get("common_patterns")
        if isinstance(patterns, str):
            patterns = [p.strip() for p in patterns.split("|") if p.strip()]
        synonyms = row.get("synonyms")
        if not isinstance(synonyms, list):
            synonyms = []

        await repo.upsert_primary_sense(
            lexeme_id,
            definition_en=(row.get("definition_en") or lemma).strip(),
            vi_gloss=row.get("vi_gloss"),
            vi_translate_prompt=row.get("vi_translate_prompt"),
            topic_prompt=row.get("topic_prompt"),
            usage_note=row.get("usage_note") or row.get("common_mistake"),
            ielts_example=row.get("example") or row.get("ielts_example"),
            gre_example=row.get("gre_example"),
            phonetic=row.get("phonetic"),
            audio_url=row.get("audio_url"),
            topic_tags=[row.get("pack_id") or "general"],
            tips=row.get("tips") if isinstance(row.get("tips"), list) else [],
            synonyms=synonyms,
        )
        stats["senses"] += 1

        storage_sense_id = str(row.get("sense_id") or "")
        if storage_sense_id:
            await repo.upsert_legacy_map(
                storage_sense_id, lexeme_id, pack_id=row.get("pack_id")
            )
            stats["legacy_maps"] += 1

        coll = _parse_collocations(row.get("collocations"))
        if coll:
            await repo.s.execute(
                delete(VocabCollocation).where(VocabCollocation.lexeme_id == lexeme_id)
            )
            for phrase, example in coll:
                repo.s.add(
                    VocabCollocation(
                        lexeme_id=lexeme_id,
                        phrase=phrase[:255],
                        example=example,
                        is_core=True,
                    )
                )
            stats["collocations"] += len(coll)

        stats["lexemes"] += 1
        if i % 500 == 0:
            await repo.s.flush()
            logger.info("Lexemes {}/{}", i, len(rows))

    logger.info("Lexeme import: {}", stats)
    return sense_to_lexeme


async def import_pack_items(
    repo: VocabLexiconRepo,
    rows: List[Dict[str, Any]],
    *,
    dry_run: bool,
) -> None:
    by_pack: Dict[str, List[Tuple[int, uuid.UUID, List[str]]]] = defaultdict(list)
    for row in rows:
        pack_id = row.get("pack_id")
        if not pack_id:
            continue
        lemma = (row.get("lemma") or "").strip().lower()
        pos = _normalize_pos(row.get("pos") or "noun")
        lid = lexeme_id_for(lemma, pos)
        idx = int(row.get("vocab_index") or 0)
        labels = infer_stat_labels(lemma)
        by_pack[str(pack_id)].append((idx, lid, labels))

    if dry_run:
        for pack_id, items in by_pack.items():
            logger.info("[dry-run] pack {} -> {} items", pack_id, len(items))
        return

    await _ensure_packs(repo, set(by_pack.keys()))

    for pack_id, items in sorted(by_pack.items()):
        items.sort(key=lambda t: t[0])
        lexeme_ids = [t[1] for t in items]
        stat_labels = [t[2] for t in items]
        sense_rows = (
            await repo.s.execute(
                select(VocabSense.lexeme_id, VocabSense.id).where(
                    VocabSense.lexeme_id.in_(lexeme_ids),
                    VocabSense.sense_order == 1,
                )
            )
        ).all()
        sense_by_lex = {row[0]: row[1] for row in sense_rows}
        sense_ids = [sense_by_lex.get(lid) for lid in lexeme_ids]
        await repo.set_pack_items(
            pack_id,
            lexeme_ids,
            sense_ids=sense_ids,
            stat_labels=stat_labels,
        )
        pack_row = (
            await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
        ).scalar_one_or_none()
        if pack_row:
            pack_row.target_word_count = len(lexeme_ids)
            pack_row.completed_word_count = len(lexeme_ids)
            pack_row.content_status = "complete"
        logger.info("Pack {} — {} items", pack_id, len(lexeme_ids))


async def import_quiz_files(
    repo: VocabLexiconRepo,
    storage: Path,
    *,
    dry_run: bool,
) -> None:
    stats = {"mcq": 0, "skipped_kind": 0, "skipped_type": 0, "missing_lexeme": 0}
    quiz_paths = sorted(storage.glob("quiz_*_vocab.json"))
    # CEFR levels first, then IELTS, then GRE (later files override same slot type).
    order = {"A1": 0, "A2": 1, "B1": 2, "B2": 3, "C1": 4, "C2": 5, "IELTS": 6, "GRE": 7}

    def sort_key(p: Path) -> Tuple[int, str]:
        try:
            level = json.loads(p.read_text(encoding="utf-8")).get("level_code") or ""
        except Exception:
            level = ""
        return (order.get(str(level).upper(), 99), p.name)

    quiz_paths = sorted(quiz_paths, key=sort_key)

    for path in quiz_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") or []
        logger.info("Quiz file {} ({} vocab items)", path.name, len(items))
        for item in items:
            ref = item.get("vocab_ref") or {}
            lemma = (ref.get("lemma") or ref.get("display_word") or "").strip().lower()
            pos = _normalize_pos(ref.get("pos") or "noun")
            if not lemma:
                continue
            lid = lexeme_id_for(lemma, pos)
            if not dry_run:
                exists = await repo.s.scalar(
                    select(VocabLexeme.id).where(VocabLexeme.id == lid)
                )
                if not exists:
                    stats["missing_lexeme"] += 1
                    continue

            for q in item.get("questions") or []:
                if q.get("interaction_kind") != "mcq":
                    stats["skipped_kind"] += 1
                    continue
                raw_type = (q.get("task_type") or "").strip()
                qtype = QUIZ_TASK_TO_DB.get(raw_type)
                if not qtype:
                    stats["skipped_type"] += 1
                    continue
                options, correct = _wire_options(q.get("options") or [])
                prompt = _question_prompt(q)
                status = _map_question_status(q.get("status"))
                slot = int(q.get("mastery_slot") or 3)
                meta = {
                    "source": "vocab_storage",
                    "storage_question_id": q.get("question_id"),
                    "storage_file": path.name,
                    "raw_task_type": raw_type,
                    "mastery_slot": slot,
                    "track_level": data.get("level_code"),
                }
                if dry_run:
                    stats["mcq"] += 1
                    continue
                sense_id = None
                if not dry_run:
                    sense_id = await repo.s.scalar(
                        select(VocabSense.id).where(
                            VocabSense.lexeme_id == lid,
                            VocabSense.sense_order == 1,
                        )
                    )
                await repo.upsert_question(
                    lid,
                    qtype=qtype,
                    prompt=prompt,
                    options=options,
                    correct_option_id=correct,
                    explanation=q.get("explanation"),
                    difficulty=max(1, min(5, slot)),
                    status=status,
                    sense_id=sense_id,
                    generator_meta=meta,
                )
                stats["mcq"] += 1
                if stats["mcq"] % 2000 == 0:
                    await repo.s.flush()
                    logger.info("Questions imported: {}", stats["mcq"])

    logger.info("Quiz import: {}", stats)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import vocab_storage into Postgres")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-questions", action="store_true")
    parser.add_argument("--skip-packs", action="store_true")
    parser.add_argument(
        "--enrich-only",
        action="store_true",
        help="Skip lexeme insert; update senses only",
    )
    parser.add_argument(
        "--skip-lexemes", action="store_true", help="Only packs + questions"
    )
    parser.add_argument("--vocab-only", action="store_true", help="Lexemes/senses only")
    args = parser.parse_args()

    storage = _storage_dir()
    vocab_path = storage / "vocab_full_table.json"
    if not vocab_path.is_file():
        raise SystemExit(f"Missing {vocab_path}")

    rows = json.loads(vocab_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("vocab_full_table.json must be a JSON array")

    logger.info("Loading {} rows from {}", len(rows), vocab_path)
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        if not args.skip_lexemes:
            await import_lexemes(
                repo, rows, dry_run=args.dry_run, enrich_only=args.enrich_only
            )
        if not args.skip_packs and not args.vocab_only:
            await import_pack_items(repo, rows, dry_run=args.dry_run)
        if not args.skip_questions and not args.vocab_only:
            await import_quiz_files(repo, storage, dry_run=args.dry_run)
        if not args.dry_run:
            await session.commit()

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
