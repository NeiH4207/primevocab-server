"""Compute difficulty_score and band columns from frequency + academic flags."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select

from aiforen.domain.sql_models import VocabLexeme
from aiforen.scripts.vocab._common import pg_session, run_async


def _score_lexeme(lx: VocabLexeme) -> float:
    score = 0.0
    if lx.frequency_rank is not None:
        if lx.frequency_rank < 500:
            score += 1.0
        elif lx.frequency_rank < 5000:
            score += 2.5
        else:
            score += 4.0
    if lx.is_academic:
        score += 2.0
    if lx.gre_tier == "hard":
        score += 2.5
    elif lx.gre_tier == "medium":
        score += 1.5
    sources = lx.sources or []
    for s in sources:
        if s.get("name") in ("AWL", "NAWL"):
            score += 1.0
    return min(10.0, score)


def _bands_from_score(
    score: float, exam_types: list
) -> tuple[float, float, str | None]:
    if "gre" in exam_types and score >= 7:
        return 8.0, 9.0, "hard"
    if score <= 2.5:
        return 4.0, 5.0, None
    if score <= 4.5:
        return 5.0, 6.0, None
    if score <= 6.0:
        return 6.0, 7.0, None
    if score <= 7.5:
        return 7.0, 8.0, "medium"
    return 8.0, 9.0, "hard"


async def main() -> None:
    async for repo in pg_session():
        stmt = select(VocabLexeme)
        lexemes = (await repo.s.execute(stmt)).scalars().all()
        for lx in lexemes:
            score = _score_lexeme(lx)
            lx.difficulty_score = score
            bmin, bmax, gre = _bands_from_score(score, lx.exam_types or ["ielts"])
            if lx.ielts_band_min is None:
                lx.ielts_band_min = bmin
            if lx.ielts_band_max is None:
                lx.ielts_band_max = bmax
            if gre and not lx.gre_tier:
                lx.gre_tier = gre
        await repo.s.flush()
        logger.info("Computed difficulty for {} lexemes", len(lexemes))


if __name__ == "__main__":
    run_async(main())
