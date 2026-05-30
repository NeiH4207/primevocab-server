"""Import NGSL into vocab_lexemes. Uses crawled CSV if present, else sample TSV."""

from __future__ import annotations

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.import_crawled import import_crawled_lexemes
from aiforen.scripts.vocab.sources import RAW_DIR


async def main() -> None:
    stats_path = RAW_DIR / "ngsl_12_stats.csv"
    if not stats_path.exists():
        logger.warning(
            "Run download_sources or crawl_all first; expected {}", stats_path
        )
        return
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        stats = await import_crawled_lexemes(VocabLexiconRepo(session))
        await session.commit()
        logger.info("NGSL import: {}", stats)


if __name__ == "__main__":
    run_async(main())
