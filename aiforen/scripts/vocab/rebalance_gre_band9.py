"""Rebalance GRE + band 9 packs without wiping the lexicon.

1. Import / refresh senses from Vocabulary.xlsx (GRE study list).
2. pack_gre ← xlsx words (season order, up to target).
3. pack_band_9 ← top NGSL/NAWL (rank 2400–2809), excluding GRE pack.
4. Words removed from old GRE / band 9 → pushed into bands 5–8 by frequency rank.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabLexeme, VocabPack, VocabPackItem
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo, lexeme_id_for
from aiforen.scripts.vocab.pack_specs import PACK_TARGET_GOALS, infer_stat_labels
from aiforen.scripts.vocab.select_pack_words import select_words_for_pack
from aiforen.scripts.vocab.xlsx_vocabulary import (
    XLSX_SOURCE,
    gre_tier_for_season,
    load_vocabulary_xlsx,
)

# Displaced lexemes land in these rank windows (NGSL frequency_rank).
PUSH_BAND_RANKS: List[Tuple[str, int, int]] = [
    ("pack_band_5", 600, 1099),
    ("pack_band_6", 1100, 1499),
    ("pack_band_7", 1500, 1899),
    ("pack_band_8", 1900, 2399),
]


def _xlsx_paths() -> List[Path]:
    base = Path(__file__).resolve().parents[3]
    return [
        base / "data" / "Vocabulary.xlsx",
        base.parent / "Vocabulary.xlsx",
    ]


def _resolve_xlsx(path: Optional[str]) -> Path:
    if path:
        p = Path(path)
        if p.is_file():
            return p
        raise FileNotFoundError(path)
    for candidate in _xlsx_paths():
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Vocabulary.xlsx not found in {_xlsx_paths()}")


async def import_vocabulary_xlsx(
    repo: VocabLexiconRepo,
    xlsx_path: Path,
) -> List[UUID]:
    """Upsert lexemes + primary senses; return lexeme ids in sheet order."""
    rows = load_vocabulary_xlsx(xlsx_path)
    ordered_ids: List[UUID] = []
    seen: Set[Tuple[str, str]] = set()

    for row in rows:
        key = (row.lemma, row.pos)
        if key in seen:
            continue
        seen.add(key)

        lid = lexeme_id_for(row.lemma, row.pos)
        lexeme = await repo.upsert_lexeme(
            lemma=row.lemma,
            pos=row.pos,
            display_word=row.lemma,
            gre_tier=gre_tier_for_season(row.season),
            is_academic=True,
            exam_types=["gre"],
            sources=[
                {
                    "name": XLSX_SOURCE,
                    "season": row.season,
                    "order": row.order,
                }
            ],
            status="approved",
        )
        await repo.upsert_primary_sense(
            lexeme.id,
            definition_en=row.definition_en,
            vi_gloss=row.vi_gloss,
            phonetic=row.phonetic,
            ielts_example=row.example,
            gre_example=row.example,
        )
        ordered_ids.append(lid)

    logger.info("Imported {} xlsx lexemes from {}", len(ordered_ids), xlsx_path)
    return ordered_ids


def _rank_bucket(rank: Optional[int], is_academic: bool) -> str:
    if rank is None:
        return "pack_band_7" if is_academic else "pack_band_6"
    for pack_id, rmin, rmax in PUSH_BAND_RANKS:
        if rmin <= rank <= rmax:
            return pack_id
    if rank < 600:
        return "pack_band_5"
    return "pack_band_8"


async def _lexemes_by_ids(
    repo: VocabLexiconRepo, ids: Set[UUID]
) -> Dict[UUID, VocabLexeme]:
    if not ids:
        return {}
    stmt = select(VocabLexeme).where(VocabLexeme.id.in_(ids))
    rows = (await repo.s.execute(stmt)).scalars().all()
    return {lx.id: lx for lx in rows}


async def merge_displaced_into_lower_bands(
    repo: VocabLexiconRepo,
    displaced_ids: Set[UUID],
    reserved_ids: Set[UUID],
    *,
    dry_run: bool = False,
) -> None:
    if not displaced_ids:
        logger.info("No displaced lexemes to push into bands 5–8")
        return

    displaced = await _lexemes_by_ids(repo, displaced_ids)
    by_band: Dict[str, List[UUID]] = {p: [] for p, _, _ in PUSH_BAND_RANKS}

    for lid in displaced_ids:
        lx = displaced.get(lid)
        if not lx:
            continue
        pack_id = _rank_bucket(lx.frequency_rank, lx.is_academic)
        by_band[pack_id].append(lid)

    for pack_id, rmin, rmax in PUSH_BAND_RANKS:
        target = PACK_TARGET_GOALS[pack_id]
        extra = by_band[pack_id]
        if not extra:
            continue

        stmt = (
            select(VocabPackItem)
            .where(VocabPackItem.pack_id == pack_id)
            .options(selectinload(VocabPackItem.lexeme))
            .order_by(VocabPackItem.order_index)
        )
        items = (await repo.s.execute(stmt)).scalars().all()
        rank_map: Dict[UUID, int] = {}
        merged: List[UUID] = []
        seen: Set[UUID] = set()

        for lid in extra:
            if lid not in seen and lid not in reserved_ids:
                merged.append(lid)
                seen.add(lid)
                lx = displaced.get(lid)
                rank_map[lid] = lx.frequency_rank if lx and lx.frequency_rank else 99999

        for item in items:
            lid = item.lexeme_id
            if lid in seen or lid in reserved_ids:
                continue
            merged.append(lid)
            seen.add(lid)
            lx = item.lexeme
            rank_map[lid] = lx.frequency_rank if lx and lx.frequency_rank else 99999

        merged.sort(key=lambda i: rank_map.get(i, 99999))
        new_ids = merged[:target]

        logger.info(
            "{}: +{} displaced → {} items (target {})",
            pack_id,
            len(extra),
            len(new_ids),
            target,
        )
        if dry_run:
            continue

        lex_map = await _lexemes_by_ids(repo, set(new_ids))
        labels = [
            infer_stat_labels(lex_map[i].lemma) if i in lex_map else ["general"]
            for i in new_ids
        ]

        await repo.set_pack_items(
            pack_id,
            new_ids,
            stat_labels=labels,
            is_core_flags=[False] * len(new_ids),
        )


async def rebalance(
    *,
    xlsx_path: Optional[str] = None,
    dry_run: bool = False,
    skip_import: bool = False,
) -> None:
    path = _resolve_xlsx(xlsx_path)

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()

    async with sm() as session:
        repo = VocabLexiconRepo(session)

        old_gre = {lid for lid, _ in await repo.list_pack_lexeme_ids("pack_gre")}
        old_band9 = {lid for lid, _ in await repo.list_pack_lexeme_ids("pack_band_9")}

        if skip_import:
            xlsx_ids = []
            stmt = select(VocabLexeme).where(VocabLexeme.status != "deprecated")
            for lx in (await session.execute(stmt)).scalars().all():
                for src in lx.sources or []:
                    if src.get("name") == XLSX_SOURCE:
                        xlsx_ids.append(lx.id)
                        break
            logger.info("skip-import: {} xlsx-tagged lexemes in DB", len(xlsx_ids))
        else:
            xlsx_ids = await import_vocabulary_xlsx(repo, path)

        gre_target = PACK_TARGET_GOALS["pack_gre"]
        gre_ids = xlsx_ids[:gre_target]
        logger.info("pack_gre → {} words (pool {})", len(gre_ids), len(xlsx_ids))

        if not dry_run:
            labels = [infer_stat_labels("")] * len(gre_ids)
            lex_rows = await _lexemes_by_ids(repo, set(gre_ids))
            labels = [
                infer_stat_labels(lex_rows[i].lemma) for i in gre_ids if i in lex_rows
            ]
            await repo.set_pack_items(
                "pack_gre",
                gre_ids,
                stat_labels=labels,
                is_core_flags=[False] * len(gre_ids),
            )

        gre_set = set(gre_ids)
        await select_words_for_pack(
            repo,
            "pack_band_9",
            exclude_lexeme_ids=gre_set,
        )
        new_band9 = {lid for lid, _ in await repo.list_pack_lexeme_ids("pack_band_9")}

        displaced = (old_gre | old_band9) - gre_set - new_band9
        reserved = gre_set | new_band9
        logger.info(
            "Displaced {} lexemes (old gre {} + band9 {} → gre {} band9 {})",
            len(displaced),
            len(old_gre),
            len(old_band9),
            len(gre_set),
            len(new_band9),
        )

        await merge_displaced_into_lower_bands(
            repo,
            displaced,
            reserved,
            dry_run=dry_run,
        )

        if not dry_run:
            for pack_id in (
                "pack_band_5",
                "pack_band_6",
                "pack_band_7",
                "pack_band_8",
            ):
                n = len(await repo.list_pack_lexeme_ids(pack_id))
                goal = PACK_TARGET_GOALS[pack_id]
                if n < goal:
                    logger.info(
                        "{} underfilled ({} < {}), topping up from rank pool",
                        pack_id,
                        n,
                        goal,
                    )
                    await select_words_for_pack(
                        repo,
                        pack_id,
                        exclude_lexeme_ids=reserved,
                    )

        for pack_id in ("pack_gre", "pack_band_9"):
            pack_row = (
                await session.execute(
                    select(VocabPack).where(VocabPack.pack_id == pack_id)
                )
            ).scalar_one_or_none()
            if pack_row and not dry_run:
                n = len(await repo.list_pack_lexeme_ids(pack_id))
                pack_row.target_word_count = n
                pack_row.completed_word_count = n
                pack_row.content_status = "filled"

        if dry_run:
            await session.rollback()
            logger.info("Dry run — rolled back")
        else:
            await session.commit()
            logger.info("Rebalance committed")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebalance GRE (xlsx) + band 9 (top NGSL)"
    )
    parser.add_argument("--xlsx", help="Path to Vocabulary.xlsx")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Only reshuffle packs; lexemes already imported from xlsx",
    )
    args = parser.parse_args()
    await rebalance(
        xlsx_path=args.xlsx,
        dry_run=args.dry_run,
        skip_import=args.skip_import,
    )


if __name__ == "__main__":
    asyncio.run(main())
