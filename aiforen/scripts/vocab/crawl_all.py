"""Crawl open word lists → Postgres lexicon (NGSL + NAWL)."""

from __future__ import annotations

import asyncio

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.compute_difficulty import main as compute_difficulty
from aiforen.scripts.vocab.download_sources import download_all
from aiforen.scripts.vocab.import_crawled import import_crawled_lexemes


async def main() -> None:
    n = await download_all()
    if n < 1:
        raise SystemExit("No source files downloaded")

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        stats = await import_crawled_lexemes(VocabLexiconRepo(session))
        await session.commit()
        logger.info("Import stats: {}", stats)

    await compute_difficulty()
    logger.info("Crawl + import complete")


if __name__ == "__main__":
    asyncio.run(main())
