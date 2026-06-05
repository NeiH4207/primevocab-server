"""Validate generated vocab questions with lightweight quality-tier checks."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import or_, select

from aiforen.domain.sql_models import VocabQuestion
from aiforen.scripts.vocab._common import pg_session, run_async


def validate_question(q: VocabQuestion) -> tuple[bool, int, list[str]]:
    interaction = (q.interaction_kind or "mcq").strip().lower()
    issues: list[str] = []
    prompt = (q.prompt or "").strip()
    if len(prompt) < 8:
        issues.append("prompt_too_short")
    if interaction != "mcq":
        payload = q.payload or {}
        if interaction == "reorder" and not payload.get("tokens"):
            issues.append("missing_reorder_tokens")
        if interaction in ("rewrite", "free_text") and not (
            payload.get("model_answer") or payload.get("corrected_sentence")
        ):
            issues.append("missing_model_answer")
        score = max(0, 100 - 25 * len(issues))
        return not issues, score, issues

    options = q.options or []
    if len(options) < 2:
        issues.append("too_few_options")
    ids = [o.get("id") for o in options]
    if len(ids) != len(set(ids)):
        issues.append("duplicate_option_ids")
    correct_count = sum(1 for o in options if o.get("id") == q.correct_option_id)
    if correct_count != 1:
        issues.append("correct_not_unique")
    texts = [str(o.get("text", "")).strip().lower() for o in options]
    if len(texts) != len(set(texts)):
        issues.append("duplicate_option_text")
    if texts and max(map(len, texts)) > max(12, min(map(len, texts)) * 4):
        issues.append("option_length_cue")
    score = max(0, 100 - 18 * len(issues))
    return not issues, score, issues


def quality_tier(score: int) -> str:
    if score >= 92:
        return "elite"
    if score >= 82:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 50:
        return "basic"
    return "bad"


async def main() -> None:
    async for repo in pg_session():
        stmt = select(VocabQuestion).where(
            or_(
                VocabQuestion.status == "generated",
                (
                    VocabQuestion.status.in_(("validated", "approved"))
                    & VocabQuestion.quality_tier.is_(None)
                ),
            )
        )
        questions = (await repo.s.execute(stmt)).scalars().all()
        validated = rejected = 0
        for q in questions:
            ok, score, issues = validate_question(q)
            q.quality_score = score
            q.quality_tier = quality_tier(score)
            q.quality_issues = issues
            if ok and q.quality_tier in ("good", "excellent", "elite"):
                if q.status == "generated":
                    q.status = "validated"
                validated += 1
            elif q.status == "generated":
                q.status = "rejected"
                q.generator_meta = {
                    **(q.generator_meta or {}),
                    "reject_reason": ",".join(issues) or "quality_below_good",
                }
                rejected += 1
        await repo.s.flush()
        logger.info("Validated: {}, rejected: {}", validated, rejected)


if __name__ == "__main__":
    run_async(main())
