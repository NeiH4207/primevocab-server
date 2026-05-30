"""Shared helpers for vocab pipeline scripts."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo


async def pg_session() -> AsyncIterator[VocabLexiconRepo]:
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        yield VocabLexiconRepo(session)
        await session.commit()


def run_async(coro):
    return asyncio.run(coro)
