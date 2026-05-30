"""Validate generated MCQs with rule checks."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select

from aiforen.domain.sql_models import VocabQuestion
from aiforen.scripts.vocab._common import pg_session, run_async


def validate_question(q: VocabQuestion) -> tuple[bool, str]:
    options = q.options or []
    if len(options) < 2:
        return False, "too_few_options"
    ids = [o.get("id") for o in options]
    if len(ids) != len(set(ids)):
        return False, "duplicate_option_ids"
    correct_count = sum(1 for o in options if o.get("id") == q.correct_option_id)
    if correct_count != 1:
        return False, "correct_not_unique"
    texts = [str(o.get("text", "")).strip().lower() for o in options]
    if len(texts) != len(set(texts)):
        return False, "duplicate_option_text"
    prompt_lower = (q.prompt or "").lower()
    if q.type == "meaning_mcq" and len(prompt_lower) < 10:
        return False, "prompt_too_short"
    return True, "ok"


async def main() -> None:
    async for repo in pg_session():
        stmt = select(VocabQuestion).where(VocabQuestion.status == "generated")
        questions = (await repo.s.execute(stmt)).scalars().all()
        validated = rejected = 0
        for q in questions:
            ok, reason = validate_question(q)
            if ok:
                q.status = "validated"
                validated += 1
            else:
                q.status = "rejected"
                q.generator_meta = {**(q.generator_meta or {}), "reject_reason": reason}
                rejected += 1
        await repo.s.flush()
        logger.info("Validated: {}, rejected: {}", validated, rejected)


if __name__ == "__main__":
    run_async(main())
