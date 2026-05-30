"""Backfill vi_translate_prompt + topic_prompt via LLM (run after fast enrich).

Not used during bulk pack fill — keeps initial UI load fast.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo

# TODO: batch Anthropic prompts for packs missing quality VI sentence tasks.


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM backfill for vocab sentence prompts"
    )
    parser.add_argument("pack_id", help="e.g. pack_gre")
    args = parser.parse_args()
    logger.warning(
        "backfill_llm_prompts for {} is not implemented yet — use fast enrich + templates for now",
        args.pack_id,
    )
    core_db.init_pg()
    _ = VocabLexiconRepo  # placeholder until implemented
    await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
