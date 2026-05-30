"""Merge split packs into unified band packs (one pack per band)."""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.build_packs import build_band_packs


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--select",
        action="store_true",
        help="Also run select_words_for_pack to fill each band pack from NGSL pool",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Run complete_pack (select+enrich) for each active band pack",
    )
    args = parser.parse_args()

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        n = await build_band_packs(repo)
        await session.commit()
        logger.info("Built {} unified band packs", n)

    if args.select or args.enrich:
        from aiforen.scripts.vocab.build_packs import BAND_PACKS
        from aiforen.scripts.vocab.complete_pack import complete_one_pack

        async with sm() as session:
            repo = VocabLexiconRepo(session)
            for spec in BAND_PACKS:
                if args.enrich:
                    await complete_one_pack(repo, spec["pack_id"], skip_select=False)
                elif args.select:
                    from aiforen.scripts.vocab.select_pack_words import (
                        select_words_for_pack,
                    )

                    await select_words_for_pack(repo, spec["pack_id"])
            await session.commit()

    logger.info("Consolidation done")


if __name__ == "__main__":
    asyncio.run(main())
