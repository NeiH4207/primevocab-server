"""Apply SUBTLEX-US frequency ranks to existing lexemes by lemma match."""

from __future__ import annotations

import csv
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from aiforen.domain.sql_models import VocabLexeme
from aiforen.scripts.vocab._common import pg_session, run_async

DATA = Path(__file__).parent / "data" / "subtlex_sample.tsv"


async def main() -> None:
    freq: dict[str, int] = {}
    with DATA.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            w = (row.get("word") or "").strip().lower()
            if w:
                freq[w] = int(row.get("frequency") or 0)

    async for repo in pg_session():
        stmt = select(VocabLexeme)
        lexemes = (await repo.s.execute(stmt)).scalars().all()
        updated = 0
        for lx in lexemes:
            if lx.lemma in freq:
                lx.frequency_rank = 100000 - freq[lx.lemma]
                updated += 1
        await repo.s.flush()
        logger.info("Updated frequency_rank for {} lexemes", updated)


if __name__ == "__main__":
    run_async(main())
