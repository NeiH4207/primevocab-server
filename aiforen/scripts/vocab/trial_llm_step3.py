"""Trial Step 3 prompts (vi_translate + topic) for GRE — one word, no DB write."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

os.environ["CORS_ORIGINS"] = '["http://localhost:3000","http://127.0.0.1:3000"]'
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger
from sqlalchemy import text

from aiforen.core import db as core_db
from aiforen.core.config import get_settings
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.mcq_llm import (
    TokenUsage,
    cost_usd,
    extract_json,
    fmt_cost,
    usage_from_response,
)


@dataclass
class GreWord:
    display_word: str
    pos: str
    definition_en: str
    example: str
    vi_gloss: str
    template_translate: str
    template_topic: str


STEP3_SCHEMA = """
Return ONLY valid JSON (no markdown):
{
  "vi_translate_prompt": "one natural Vietnamese sentence the learner must translate into English; the English answer must require the target word (or a natural inflection)",
  "topic_prompt": "Vietnamese instruction to write ONE English sentence using the target word in the given sense; name a concrete academic/GRE-style situation",
  "quality_notes": "brief English note on why these prompts test real usage"
}
"""


def build_gre_step3_prompt(w: GreWord) -> str:
    return f"""You write Step 3 vocabulary practice prompts for Vietnamese learners studying GRE / academic English.

Target word: {w.display_word} ({w.pos})
English definition (authoritative): {w.definition_en}
Reference example: {w.example}
Vietnamese gloss: {w.vi_gloss}

Requirements:
- vi_translate_prompt: ONE complete Vietnamese sentence (not meta-instructions). The learner translates it to English and MUST use "{w.display_word}" (or natural inflection) correctly in the target sense. Avoid generic filler like "Nhiều người cho rằng cuộc sống và xã hội rất quan trọng."
- topic_prompt: ONE Vietnamese sentence telling the learner what English sentence to write. Specify a concrete context (research, policy, debate, science, ethics, etc.). Must require "{w.display_word}" in the correct sense. Do NOT mention IELTS — this is GRE vocabulary.
- Prompts must be specific to THIS word's meaning, not interchangeable with other words.
- Vietnamese should be natural for Vietnamese university students.

{STEP3_SCHEMA}"""


def score_prompts(w: GreWord, generated: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    trans = (generated.get("vi_translate_prompt") or "").strip()
    topic = (generated.get("topic_prompt") or "").strip()
    word = w.display_word.lower()

    if len(trans) < 25:
        issues.append("translate prompt too short")
    if len(topic) < 25:
        issues.append("topic prompt too short")
    if "ielts" in trans.lower() or "ielts" in topic.lower():
        issues.append("mentions IELTS (should be GRE/academic)")
    if "nhiều người cho rằng cuộc sống" in trans.lower():
        issues.append("uses generic template filler sentence")
    if word not in trans.lower() and word not in topic.lower():
        issues.append(
            "target word not referenced in prompts (may still be OK if implied)"
        )
    if trans.strip() == w.template_translate.strip():
        issues.append("identical to current DB template")
    if "dịch sang tiếng anh (dùng từ" in trans.lower()[:40]:
        issues.append(
            "translate prompt is meta-instruction wrapper, not a VI sentence to translate"
        )
    if not issues:
        issues.append("(heuristic) looks good for trial")
    return issues


async def fetch_gre_word(
    repo: VocabLexiconRepo, *, word: Optional[str] = None
) -> GreWord:
    sql = """
    SELECT l.display_word, l.pos, s.definition_en,
           coalesce(nullif(trim(s.gre_example),''), nullif(trim(s.ielts_example),'')) AS ex,
           s.vi_gloss, s.vi_translate_prompt, s.topic_prompt
    FROM vocab_pack_items i
    JOIN vocab_lexemes l ON l.id = i.lexeme_id
    JOIN vocab_senses s ON s.lexeme_id = l.id AND s.sense_order = 1
    WHERE i.pack_id = 'pack_gre'
      AND length(trim(coalesce(s.definition_en,''))) > 10
      AND length(trim(coalesce(s.vi_gloss,''))) > 2
    """
    params: Dict[str, Any] = {}
    if word:
        sql += " AND lower(l.display_word) = :w"
        params["w"] = word.lower()
    else:
        sql += " AND lower(l.display_word) = 'abate'"
    sql += " LIMIT 1"
    row = (await repo.s.execute(text(sql), params)).one_or_none()
    if not row:
        raise RuntimeError(f"GRE word not found: {word or 'abate'}")
    return GreWord(
        display_word=row[0],
        pos=row[1] or "word",
        definition_en=row[2] or row[0],
        example=row[3] or "",
        vi_gloss=row[4] or "",
        template_translate=row[5] or "",
        template_topic=row[6] or "",
    )


async def generate_step3(
    w: GreWord, *, model: str
) -> tuple[Dict[str, Any], TokenUsage]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=model,
        max_tokens=600,
        temperature=0.35,
        messages=[{"role": "user", "content": build_gre_step3_prompt(w)}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    return extract_json(raw), usage_from_response(msg)


async def trial_evaluate(
    w: GreWord,
    vi_translate: str,
    topic_vi: str,
    *,
    model: str,
    sample_translate_en: str,
    sample_topic_en: str,
) -> Dict[str, Any]:
    """Run same contract as production evaluate_vocab_sentence."""
    settings = get_settings()
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = (
        "You are a GRE/academic English vocabulary coach. The student writes two short English "
        "sentences using a target word: (1) translation of a Vietnamese sentence, (2) free composition. "
        "Return ONLY valid JSON.\n"
        "Schema:\n"
        "{\n"
        '  "translate": {"is_grammatically_ok": boolean, "corrected_sentence": string, "recommendation": string},\n'
        '  "topic": {"is_grammatically_ok": boolean, "corrected_sentence": string, "recommendation": string},\n'
        '  "band_style_tip": string\n'
        "}\n\n"
        f"Target word: {w.display_word}\n"
        f"Definition: {w.definition_en}\n"
        f"Translation prompt (Vietnamese): {vi_translate}\n"
        f"Student's English translation: {sample_translate_en}\n"
        f"Topic prompt (Vietnamese): {topic_vi}\n"
        f"Student's English topic sentence: {sample_topic_en}\n"
    )
    msg = await client.messages.create(
        model=model,
        max_tokens=700,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    return extract_json(raw)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Trial GRE Step 3 prompts with Claude")
    parser.add_argument(
        "--word", default="abate", help="GRE display_word (default: abate)"
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--with-eval",
        action="store_true",
        help="Also call Claude to grade sample student sentences",
    )
    args = parser.parse_args()

    model = args.model or get_settings().anthropic_model
    logger.info("Model: {}", model)

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        w = await fetch_gre_word(repo, word=args.word)

    print(f"\n{'='*72}\nGRE Step 3 trial: {w.display_word} ({w.pos})")
    print(f"Def: {w.definition_en[:120]}")
    print(f"Ex:  {w.example[:120]}")
    print(f"VI gloss: {w.vi_gloss}")

    print(f"\n--- CURRENT (template in DB) ---")
    print(f"Translate prompt:\n  {w.template_translate}")
    print(f"Topic prompt:\n  {w.template_topic}")

    data, usage = await generate_step3(w, model=model)
    cost = cost_usd(model, usage)
    print(f"\n--- CLAUDE ({model}) ---")
    print(
        f"Tokens: in={usage.input_tokens} out={usage.output_tokens} → {fmt_cost(cost)}"
    )
    print(f"vi_translate_prompt:\n  {data.get('vi_translate_prompt', '')}")
    print(f"topic_prompt:\n  {data.get('topic_prompt', '')}")
    print(f"quality_notes: {data.get('quality_notes', '')}")

    issues = score_prompts(w, data)
    print(f"\n--- HEURISTIC REVIEW ---")
    for i in issues:
        print(f"  • {i}")

    if args.with_eval:
        vi_t = str(data.get("vi_translate_prompt", ""))
        vi_topic = str(data.get("topic_prompt", ""))
        # Plausible weak student attempts for smoke test
        samples = {
            "translate": f"The storm began to {w.display_word} after midnight.",
            "topic": f"Researchers hope the new policy will {w.display_word} inflation over time.",
        }
        print(f"\n--- SAMPLE STUDENT SENTENCES (for eval smoke test) ---")
        print(f"  Translate EN: {samples['translate']}")
        print(f"  Topic EN: {samples['topic']}")
        ev = await trial_evaluate(
            w,
            vi_t,
            vi_topic,
            model=model,
            sample_translate_en=samples["translate"],
            sample_topic_en=samples["topic"],
        )
        print(f"\n--- AI EVAL (production-style) ---")
        print(json.dumps(ev, indent=2, ensure_ascii=False))

    print(f"\n{'='*72}")
    print("Verdict: if prompts are word-specific and VI translate is a real sentence,")
    print("safe to design batch_llm_step3 for pack_gre (~881 words, ~$0.50-1 batch).")


if __name__ == "__main__":
    asyncio.run(main())
