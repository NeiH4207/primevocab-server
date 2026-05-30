"""Import Oxford 5000 CSV into CEFR packs (pack_oxford_a1 … pack_oxford_c1).

Does not modify IELTS band or GRE packs.

Usage:
  python -m aiforen.scripts.vocab.import_oxford_csv
  python -m aiforen.scripts.vocab.import_oxford_csv --csv data/oxford_5000_thanhtuan.csv --dry-run
"""

from __future__ import annotations

import os

# CLI outside Docker: .env may set CORS_ORIGINS as comma-separated (invalid JSON for Settings).
os.environ["CORS_ORIGINS"] = '["http://localhost:3000","http://127.0.0.1:3000"]'
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

import argparse
import asyncio
import csv
from pathlib import Path
from typing import Dict, List
from uuid import UUID

from loguru import logger
from sqlalchemy import select

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabPack
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.oxford_packs import (
    CEFR_IELTS_BAND,
    LEVEL_TO_PACK,
    OXFORD_CSV_NAME,
    OXFORD_PACKS,
    OXFORD_SOURCE,
    canonical_lemma,
    normalize_pos,
)
from aiforen.scripts.vocab.pack_specs import infer_stat_labels


def _default_csv() -> Path:
    base = Path(__file__).resolve().parents[3]
    for candidate in (
        base / "data" / OXFORD_CSV_NAME,
        base.parent / "py-server" / "data" / OXFORD_CSV_NAME,
    ):
        if candidate.is_file():
            return candidate
    return base / "data" / OXFORD_CSV_NAME


def _load_rows(csv_path: Path) -> List[dict]:
    with csv_path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def import_oxford(
    repo: VocabLexiconRepo,
    rows: List[dict],
    *,
    dry_run: bool = False,
) -> None:
    by_pack: Dict[str, List[UUID]] = {spec["pack_id"]: [] for spec in OXFORD_PACKS}

    for spec in OXFORD_PACKS:
        level = spec["oxford_level"]
        band_min, band_max = CEFR_IELTS_BAND[level]
        await repo.upsert_pack(
            {
                "pack_id": spec["pack_id"],
                "title": spec["title"],
                "description": spec["description"],
                "category": spec["category"],
                "task_type": "Both",
                "exam_type": spec["exam_type"],
                "pack_family": spec["pack_family"],
                "skill_focus": None,
                "topic": None,
                "source_band_min": band_min,
                "source_band_max": band_max,
                "target_band_min": band_min,
                "target_band_max": band_max,
                "sort_order": spec["sort_order"],
                "is_active": True,
                "is_premium": spec.get("is_premium", False),
                "content_status": "filled",
                "target_word_count": 0,
                "completed_word_count": 0,
            }
        )

    imported = 0
    skipped = 0
    for row in rows:
        level = (row.get("level") or "").strip().upper()
        pack_id = LEVEL_TO_PACK.get(level)
        if not pack_id:
            skipped += 1
            continue

        lemma = canonical_lemma(row.get("lemma") or "")
        if not lemma:
            skipped += 1
            continue

        pos = normalize_pos(row.get("pos") or "")
        display = (row.get("lemma") or lemma).strip()
        english_raw = (row.get("english_raw") or display).strip()
        vi = (row.get("vi_gloss") or "").strip() or None
        ipa = (row.get("phonetic") or "").strip() or None
        stt = row.get("stt", "")

        band_min, band_max = CEFR_IELTS_BAND[level]
        lexeme = await repo.upsert_lexeme(
            lemma=lemma,
            pos=pos,
            display_word=display,
            cefr_level=level,
            ielts_band_min=band_min,
            ielts_band_max=band_max,
            is_academic=False,
            exam_types=["oxford", "general"],
            sources=[
                {
                    "name": OXFORD_SOURCE,
                    "level": level,
                    "stt": stt,
                }
            ],
            status="approved",
        )
        await repo.upsert_primary_sense(
            lexeme.id,
            definition_en=english_raw,
            vi_gloss=vi,
            phonetic=ipa,
            topic_tags=[lemma],
        )
        ids = by_pack[pack_id]
        if lexeme.id not in ids:
            ids.append(lexeme.id)
            imported += 1
        else:
            skipped += 1

    for spec in OXFORD_PACKS:
        pack_id = spec["pack_id"]
        ids = by_pack[pack_id]
        labels = []
        for row in rows:
            lv = (row.get("level") or "").strip().upper()
            if LEVEL_TO_PACK.get(lv) != pack_id:
                continue
            labels.append(infer_stat_labels(canonical_lemma(row.get("lemma") or "")))

        await repo.set_pack_items(
            pack_id,
            ids,
            stat_labels=labels,
            is_core_flags=[False] * len(ids),
        )
        pack_row = (
            await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
        ).scalar_one_or_none()
        if pack_row:
            pack_row.target_word_count = len(ids)
            pack_row.completed_word_count = len(ids)
            pack_row.content_status = "filled"

        logger.info("Pack {} → {} words", pack_id, len(ids))

    logger.info("Imported {} lexeme rows (skipped {})", imported, skipped)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Oxford 5000 CSV into CEFR packs"
    )
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    csv_path = args.csv or _default_csv()
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")

    rows = _load_rows(csv_path)
    logger.info("Loaded {} rows from {}", len(rows), csv_path)

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        await import_oxford(repo, rows, dry_run=args.dry_run)
        if not args.dry_run:
            await session.commit()
            logger.info("Committed Oxford CEFR packs")


if __name__ == "__main__":
    asyncio.run(main())
