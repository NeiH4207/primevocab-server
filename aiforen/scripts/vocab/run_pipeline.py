"""Run full vocab lexicon pipeline (bootstrap → questions → packs)."""

from __future__ import annotations

import asyncio

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.bootstrap import (
    bootstrap_lexemes_from_legacy,
    bootstrap_questions_for_all,
)
from aiforen.scripts.vocab.build_packs import build_thematic_packs
from aiforen.scripts.vocab.compute_difficulty import main as compute_difficulty
from aiforen.scripts.vocab.enrich_lexemes import main as enrich_lexemes
from aiforen.scripts.vocab.generate_questions import main as generate_questions
from aiforen.scripts.vocab.import_awl_nawl import main as import_awl
from aiforen.scripts.vocab.import_ngsl import main as import_ngsl
from aiforen.scripts.vocab.import_subtlex import main as import_subtlex
from aiforen.scripts.vocab.validate_questions import main as validate_questions


async def main() -> None:
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        await repo.clear_all_vocab_content()
        await session.commit()

    await import_ngsl()
    await import_awl()
    await import_subtlex()

    async with sm() as session:
        repo = VocabLexiconRepo(session)
        await bootstrap_lexemes_from_legacy(repo, approve=True)
        await bootstrap_questions_for_all(repo)
        await build_thematic_packs(repo)
        await session.commit()

    await compute_difficulty()
    await enrich_lexemes()
    await generate_questions()
    await validate_questions()

    from sqlalchemy import select

    from aiforen.domain.sql_models import VocabQuestion

    async with sm() as session:
        repo = VocabLexiconRepo(session)
        stmt = select(VocabQuestion).where(VocabQuestion.status == "validated")
        for q in (await session.execute(stmt)).scalars().all():
            q.status = "approved"
        await session.commit()

    logger.info("Vocab pipeline complete")


if __name__ == "__main__":
    asyncio.run(main())
