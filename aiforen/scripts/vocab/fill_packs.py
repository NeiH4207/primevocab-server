"""Fill packs to target word counts — pack_items first; enrich/translate later.

By default only `vocab_pack_items` are written. Stub senses are optional (slow).
Words without senses still appear in the API via lexeme_to_word_dto fallback.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabLexeme, VocabPack, VocabPackItem
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.build_packs import BAND_PACKS
from aiforen.scripts.vocab.pack_specs import get_pack_spec
from aiforen.scripts.vocab.select_pack_words import select_words_for_pack


async def ensure_stub_sense(repo: VocabLexiconRepo, lexeme: VocabLexeme) -> None:
    if repo._primary_sense(lexeme):
        return
    await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=lexeme.display_word,
        vi_gloss=None,
        vi_translate_prompt=None,
        topic_prompt=None,
        usage_note=None,
        ielts_example=None,
        topic_tags=[lexeme.lemma],
        tips=[],
    )
    if lexeme.status != "deprecated":
        lexeme.status = "draft"
    await repo.s.flush()


async def fill_one_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    stub_senses: bool = False,
) -> int:
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack_id: {pack_id}")

    pack_row = (
        await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
    ).scalar_one_or_none()
    if not pack_row:
        raise ValueError(f"Pack not found: {pack_id} — run build_band_packs first")

    pack_row.content_status = "selecting"
    await repo.s.flush()

    chosen = await select_words_for_pack(repo, pack_id)
    n = len(chosen)

    if stub_senses:
        stmt = (
            select(VocabPackItem)
            .where(VocabPackItem.pack_id == pack_id)
            .options(
                selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.senses)
            )
        )
        items = (await repo.s.execute(stmt)).scalars().all()
        for i, item in enumerate(items):
            if item.lexeme:
                await ensure_stub_sense(repo, item.lexeme)
            if (i + 1) % 200 == 0:
                logger.info("{} stub senses {}/{}", pack_id, i + 1, len(items))

    pack_row.target_word_count = n
    pack_row.completed_word_count = 0
    pack_row.content_status = "filled"
    await repo.s.flush()
    logger.info(
        "Filled pack {} with {} words (stub_senses={})", pack_id, n, stub_senses
    )
    return n


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill vocab packs to target counts (no enrich)"
    )
    parser.add_argument("pack_id", nargs="?", help="Single pack, e.g. pack_gre")
    parser.add_argument(
        "--all", action="store_true", help="Fill all unified band + GRE packs"
    )
    parser.add_argument(
        "--stub-sense",
        action="store_true",
        help="Also insert minimal vocab_senses (slower; usually unnecessary)",
    )
    args = parser.parse_args()

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()

    packs = (
        [spec["pack_id"] for spec in BAND_PACKS]
        if args.all
        else ([args.pack_id] if args.pack_id else [])
    )
    if not packs:
        parser.print_help()
        return

    for pack_id in packs:
        async with sm() as session:
            repo = VocabLexiconRepo(session)
            await fill_one_pack(repo, pack_id, stub_senses=args.stub_sense)
            await session.commit()
        logger.info("Committed {}", pack_id)

    logger.info("Fill packs done ({} packs)", len(packs))


if __name__ == "__main__":
    asyncio.run(main())
