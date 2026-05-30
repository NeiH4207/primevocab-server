"""Generate MCQ / cloze questions for lexemes missing approved questions."""

from __future__ import annotations

import random

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.domain.sql_models import VocabLexeme
from aiforen.scripts.vocab._common import pg_session, run_async
from aiforen.scripts.vocab.bootstrap import _default_mcq

DISTRACTOR_TEMPLATES = [
    "The results were completely unrelated to the topic.",
    "Many people enjoy going to the beach on weekends.",
    "The government announced a new tax policy yesterday.",
]


def _cloze_mcq(word: str, example: str) -> tuple:
    blanked = (
        example.replace(word, "______", 1)
        if word in example
        else f"______ is important in this context."
    )
    options = [
        {"id": "a", "text": word},
        {"id": "b", "text": random.choice(DISTRACTOR_TEMPLATES).split()[0]},
        {"id": "c", "text": "however"},
        {"id": "d", "text": "therefore"},
    ]
    return (
        f"Choose the best word: {blanked}",
        options,
        "a",
        f"'{word}' fits the sentence naturally.",
    )


async def main() -> None:
    async for repo in pg_session():
        stmt = (
            select(VocabLexeme)
            .where(VocabLexeme.status.in_(("enriched", "approved")))
            .options(
                selectinload(VocabLexeme.senses), selectinload(VocabLexeme.questions)
            )
        )
        lexemes = (await repo.s.execute(stmt)).scalars().all()
        created = 0
        for lx in lexemes:
            has_approved = any(
                q.status in ("validated", "approved") for q in (lx.questions or [])
            )
            if has_approved:
                continue
            sense = repo._primary_sense(lx)
            if not sense:
                continue
            example = sense.ielts_example or f"This shows how to use {lx.display_word}."
            for qtype, builder in (
                ("meaning_mcq", lambda: _default_mcq(lx.display_word, example)),
                ("cloze", lambda: _cloze_mcq(lx.display_word, example)),
            ):
                prompt, options, correct, expl = builder()
                await repo.upsert_question(
                    lx.id,
                    qtype=qtype,
                    prompt=prompt,
                    options=options,
                    correct_option_id=correct,
                    explanation=expl,
                    status="generated",
                    sense_id=sense.id,
                    generator_meta={"source": "generate_questions", "version": "1"},
                )
                created += 1
        logger.info("Generated {} questions", created)


if __name__ == "__main__":
    run_async(main())
