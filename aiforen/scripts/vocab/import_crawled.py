"""Import crawled raw CSVs into vocab_lexemes (+ word forms)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select

from aiforen.domain.sql_models import VocabWordForm
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.parsers import (
    LemmaRow,
    band_from_ngsl_rank,
    merge_forms_into_rows,
    parse_lemmatized_research,
    parse_nawl_research,
    parse_ngsl_stats,
)
from aiforen.scripts.vocab.sources import SOURCES


def _paths() -> Dict[str, Path]:
    return {sf.filename: sf.local_path for sf in SOURCES}


async def import_crawled_lexemes(repo: VocabLexiconRepo) -> Dict[str, Any]:
    paths = _paths()
    stats = {"ngsl": 0, "nawl": 0, "forms": 0, "skipped": 0}

    ngsl_stats = paths.get("ngsl_12_stats.csv")
    ngsl_forms = paths.get("ngsl_12_lemmatized_research.csv")
    nawl_file = paths.get("nawl_12_lemmatized_research.csv")

    if not ngsl_stats or not ngsl_stats.exists():
        raise FileNotFoundError(f"Missing {ngsl_stats}; run download_sources first")

    ngsl_rows: List[LemmaRow] = list(parse_ngsl_stats(ngsl_stats))
    if ngsl_forms and ngsl_forms.exists():
        forms_map = parse_lemmatized_research(ngsl_forms, source="NGSL")
        ngsl_rows = merge_forms_into_rows(ngsl_rows, forms_map)

    for row in ngsl_rows:
        if not row.frequency_rank:
            stats["skipped"] += 1
            continue
        bmin, bmax = band_from_ngsl_rank(row.frequency_rank)
        lexeme = await repo.upsert_lexeme(
            lemma=row.lemma,
            pos=row.pos,
            frequency_rank=row.frequency_rank,
            ielts_band_min=bmin,
            ielts_band_max=bmax,
            exam_types=["ielts"],
            sources=[
                {
                    "name": "NGSL",
                    "rank": row.frequency_rank,
                    "sfi": row.sfi,
                    "license": "CC-BY-SA-4.0",
                }
            ],
            status="draft",
        )
        for form in row.word_forms[:6]:
            if form == row.lemma:
                continue
            existing = (
                await repo.s.execute(
                    select(VocabWordForm).where(
                        VocabWordForm.lexeme_id == lexeme.id,
                        VocabWordForm.form == form,
                    )
                )
            ).scalar_one_or_none()
            if not existing:
                repo.s.add(
                    VocabWordForm(
                        lexeme_id=lexeme.id,
                        form=form,
                        pos=row.pos,
                    )
                )
                stats["forms"] += 1
        stats["ngsl"] += 1

    if nawl_file and nawl_file.exists():
        for row in parse_nawl_research(nawl_file):
            lexeme = await repo.upsert_lexeme(
                lemma=row.lemma,
                pos=row.pos,
                is_academic=True,
                ielts_band_min=6.5,
                ielts_band_max=8.5,
                exam_types=["ielts", "gre"],
                sources=[{"name": "NAWL", "license": "CC-BY-SA-4.0"}],
                status="draft",
            )
            for form in row.word_forms[:6]:
                if form == row.lemma:
                    continue
                existing = (
                    await repo.s.execute(
                        select(VocabWordForm).where(
                            VocabWordForm.lexeme_id == lexeme.id,
                            VocabWordForm.form == form,
                        )
                    )
                ).scalar_one_or_none()
                if not existing:
                    repo.s.add(
                        VocabWordForm(
                            lexeme_id=lexeme.id,
                            form=form,
                            pos=row.pos,
                        )
                    )
                    stats["forms"] += 1
            stats["nawl"] += 1

    await repo.s.flush()
    logger.info(
        "Imported NGSL={}, NAWL={}, word_forms={}",
        stats["ngsl"],
        stats["nawl"],
        stats["forms"],
    )
    return stats
