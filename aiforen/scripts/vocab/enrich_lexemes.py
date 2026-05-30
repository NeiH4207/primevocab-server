"""Rule-based enrichment for draft lexemes (senses, collocations) without LLM."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select

from aiforen.domain.sql_models import VocabCollocation, VocabLexeme, VocabSense
from aiforen.scripts.vocab._common import pg_session, run_async


async def main() -> None:
    async for repo in pg_session():
        stmt = select(VocabLexeme).where(VocabLexeme.status == "draft")
        lexemes = (await repo.s.execute(stmt)).scalars().all()
        enriched = 0
        for lx in lexemes:
            existing_sense = (
                await repo.s.execute(
                    select(VocabSense).where(
                        VocabSense.lexeme_id == lx.id, VocabSense.sense_order == 1
                    )
                )
            ).scalar_one_or_none()
            if not existing_sense:
                await repo.upsert_primary_sense(
                    lx.id,
                    definition_en=f"A common {lx.pos} used in academic English.",
                    vi_gloss=f"(nghĩa cơ bản của {lx.display_word})",
                    vi_translate_prompt=f"Dịch sang tiếng Anh: {lx.display_word} rất quan trọng.",
                    topic_prompt=f"Write a sentence using '{lx.display_word}' about education.",
                    ielts_example=f"The data shows that {lx.display_word} plays a key role.",
                    usage_note="Use in formal writing when the meaning is clear.",
                )
            coll = (
                await repo.s.execute(
                    select(VocabCollocation)
                    .where(VocabCollocation.lexeme_id == lx.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not coll:
                repo.s.add(
                    VocabCollocation(
                        lexeme_id=lx.id,
                        phrase=f"{lx.display_word} issue",
                        pattern=f"{lx.display_word} + noun",
                        example=f"This {lx.display_word} issue requires attention.",
                        band_min=float(lx.ielts_band_min or 6),
                        is_core=True,
                    )
                )
            lx.status = "enriched"
            enriched += 1
        await repo.s.flush()
        logger.info("Enriched {} draft lexemes", enriched)


if __name__ == "__main__":
    run_async(main())
