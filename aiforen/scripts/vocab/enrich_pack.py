"""Enrich all lexemes in one pack: dictionary lookup + senses + questions."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.core.config import get_settings
from aiforen.domain.sql_models import (
    VocabCollocation,
    VocabLexeme,
    VocabPackItem,
    VocabSense,
)
from aiforen.integrations.translate import get_translate_client
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.bootstrap import _default_mcq
from aiforen.scripts.vocab.generate_questions import _cloze_mcq
from aiforen.scripts.vocab.pack_specs import get_pack_spec
from aiforen.scripts.vocab.validate_questions import validate_question

_DICTIONARY_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
# Stat topic labels → Vietnamese context (analytics label only; not separate packs).
_STAT_LABEL_VI: Dict[str, str] = {
    "education": "giáo dục",
    "health": "sức khỏe",
    "environment": "môi trường",
    "technology": "công nghệ",
    "society": "xã hội",
    "work": "công việc và kinh tế",
    "government": "chính phủ và chính sách",
    "general": "cuộc sống và xã hội",
}


async def _fetch_dictionary(word: str) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_DICTIONARY_URL.format(word=word.lower()))
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data[0] if isinstance(data, list) and data else None
    except Exception as exc:
        logger.debug("Dictionary miss {}: {}", word, exc)
        return None


def _pick_definition(
    entry: Dict[str, Any], pos_hint: str
) -> tuple[str, str, List[str]]:
    meanings = entry.get("meanings") or []
    pos_hint = pos_hint.lower()
    for meaning in meanings:
        mpos = (meaning.get("partOfSpeech") or "").lower()
        if pos_hint and mpos and pos_hint not in mpos and mpos not in pos_hint:
            continue
        for d in meaning.get("definitions") or []:
            defn = str(d.get("definition") or "").strip()
            if len(defn) < 8:
                continue
            example = str(d.get("example") or "").strip()
            syns = (d.get("synonyms") or [])[:4]
            return defn, example, syns
    for meaning in meanings:
        for d in meaning.get("definitions") or []:
            defn = str(d.get("definition") or "").strip()
            if defn:
                return defn, str(d.get("example") or "").strip(), []
    return "", "", []


def _vi_context_from_labels(labels: List[str]) -> str:
    for label in labels:
        if label in _STAT_LABEL_VI:
            return _STAT_LABEL_VI[label]
    return _STAT_LABEL_VI["general"]


def _vi_translate_prompt(vi_context: str, word: str) -> str:
    return f"Dịch sang tiếng Anh (dùng từ '{word}'): {vi_context}."


def _topic_prompt(vi_context: str, word: str) -> str:
    return (
        f"Viết một câu tiếng Anh dùng '{word}' trong ngữ cảnh IELTS "
        f"(Speaking, Writing hoặc giải thích quan điểm) về {vi_context}."
    )


async def _llm_enrich_optional(
    *,
    word: str,
    definition: str,
    topic: str,
    band: float,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if settings.llm_provider == "mock" or not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic

        anthropic = AsyncAnthropic(api_key=api_key)
        prompt = f"""For IELTS band {band} learners, enrich the word "{word}" (definition: {definition}).
Topic: {topic}.
Return JSON only:
{{
  "vi_gloss": "short Vietnamese gloss",
  "vi_translate_prompt": "Vietnamese sentence to translate using the word",
  "topic_prompt": "Vietnamese instruction to write an English sentence with the word",
  "ielts_example": "one natural IELTS-style example sentence",
  "collocations": ["phrase 1", "phrase 2"],
  "usage_note": "one line usage tip",
  "common_mistake": "one typical learner error"
}}"""
        msg = await anthropic.messages.create(
            model=settings.anthropic_model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text if msg.content else "{}"
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            return json.loads(match.group(0))
    except Exception as exc:
        logger.warning("LLM enrich skipped for {}: {}", word, exc)
    return None


async def enrich_lexeme_in_pack(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    stat_labels: List[str],
    target_band: float,
    pack_family: str = "band",
) -> None:
    entry = await _fetch_dictionary(lexeme.display_word)
    definition = ""
    example = ""
    if entry:
        definition, example, _ = _pick_definition(entry, lexeme.pos)
    if not definition:
        definition = f"A common {lexeme.pos} used in academic English."

    vi_context = _vi_context_from_labels(stat_labels)
    llm_extra = await _llm_enrich_optional(
        word=lexeme.display_word,
        definition=definition,
        topic=vi_context,
        band=target_band,
    )

    vi_gloss = (llm_extra or {}).get("vi_gloss") or f"(nghĩa của {lexeme.display_word})"
    translate_prompt = (llm_extra or {}).get(
        "vi_translate_prompt"
    ) or _vi_translate_prompt(
        f"Nhiều người cho rằng {vi_context} rất quan trọng", lexeme.display_word
    )
    topic_prompt = (llm_extra or {}).get("topic_prompt") or _topic_prompt(
        vi_context, lexeme.display_word
    )
    default_example = (
        f"Many people believe that {lexeme.display_word} is important when discussing {vi_context}."
        if pack_family == "band"
        else f"The passage suggests that {lexeme.display_word} is central to the argument."
    )
    ielts_example = (llm_extra or {}).get("ielts_example") or example or default_example
    usage_note = (llm_extra or {}).get("usage_note") or (
        "Use naturally in IELTS — listening, reading, speaking, or writing."
        if pack_family == "band"
        else "Typical of formal academic English."
    )

    sense = await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=definition,
        vi_gloss=vi_gloss,
        vi_translate_prompt=translate_prompt,
        topic_prompt=topic_prompt,
        usage_note=usage_note,
        ielts_example=ielts_example,
        topic_tags=list(stat_labels) + [lexeme.lemma],
        tips=[
            "Use when the meaning is clear from context.",
            "Works across IELTS skills — not tied to one task type.",
        ],
    )

    coll_phrases = (llm_extra or {}).get("collocations") or [
        f"{lexeme.display_word} issue",
        f"significant {lexeme.display_word}",
    ]
    for phrase in coll_phrases[:3]:
        existing = (
            await repo.s.execute(
                select(VocabCollocation).where(
                    VocabCollocation.lexeme_id == lexeme.id,
                    VocabCollocation.phrase == phrase,
                )
            )
        ).scalar_one_or_none()
        if not existing:
            repo.s.add(
                VocabCollocation(
                    lexeme_id=lexeme.id,
                    phrase=str(phrase),
                    example=f"This {phrase} is widely discussed.",
                    band_min=float(lexeme.ielts_band_min or target_band),
                    is_core=True,
                )
            )

    example_for_mcq = ielts_example
    prompt, options, correct, expl = _default_mcq(lexeme.display_word, example_for_mcq)
    await repo.upsert_question(
        lexeme.id,
        qtype="meaning_mcq",
        prompt=prompt,
        options=options,
        correct_option_id=correct,
        explanation=expl,
        status="approved",
        sense_id=sense.id,
        generator_meta={"source": "enrich_pack"},
    )
    cp, copts, cc, ce = _cloze_mcq(lexeme.display_word, example_for_mcq)
    q2 = await repo.upsert_question(
        lexeme.id,
        qtype="cloze",
        prompt=cp,
        options=copts,
        correct_option_id=cc,
        explanation=ce,
        status="generated",
        sense_id=sense.id,
    )
    ok, _ = validate_question(q2)
    q2.status = "approved" if ok else "validated"

    lexeme.status = "approved"
    await repo.s.flush()


def _fast_definition_en(lexeme: VocabLexeme, pack_family: str) -> str:
    pos = lexeme.pos or "word"
    if pack_family == "gre":
        return f"A formal {pos} common on GRE Verbal."
    return f"A common {pos} used in IELTS academic English."


def _fast_example(lexeme: VocabLexeme, pack_family: str, vi_context: str) -> str:
    w = lexeme.display_word
    if pack_family == "gre":
        return f"The passage uses {w} to sharpen the author's claim."
    return f"Many people believe that {w} matters when discussing {vi_context}."


def _gloss_translate_query(lexeme: VocabLexeme) -> str:
    """Single string sent to Google Translate for VI gloss."""
    pos = (lexeme.pos or "").strip()
    if pos:
        return f"{lexeme.display_word} ({pos})"
    return lexeme.display_word


async def _upsert_sense_and_questions(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    definition_en: str,
    vi_gloss: str,
    translate_prompt: str,
    topic_prompt: str,
    ielts_example: str,
    usage_note: str,
    stat_labels: List[str],
    pack_family: str,
    target_band: float,
) -> None:
    sense = await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=definition_en,
        vi_gloss=vi_gloss,
        vi_translate_prompt=translate_prompt,
        topic_prompt=topic_prompt,
        usage_note=usage_note,
        ielts_example=ielts_example,
        gre_example=ielts_example if pack_family == "gre" else None,
        topic_tags=list(stat_labels) + [lexeme.lemma],
        tips=[
            "Use when the meaning is clear from context.",
            "Works across IELTS skills — not tied to one task type.",
        ],
    )

    for phrase in (
        f"{lexeme.display_word} issue",
        f"significant {lexeme.display_word}",
    )[:2]:
        existing = (
            await repo.s.execute(
                select(VocabCollocation).where(
                    VocabCollocation.lexeme_id == lexeme.id,
                    VocabCollocation.phrase == phrase,
                )
            )
        ).scalar_one_or_none()
        if not existing:
            repo.s.add(
                VocabCollocation(
                    lexeme_id=lexeme.id,
                    phrase=phrase,
                    example=f"This {phrase} is widely discussed.",
                    band_min=float(lexeme.ielts_band_min or target_band),
                    is_core=True,
                )
            )

    prompt, options, correct, expl = _default_mcq(lexeme.display_word, ielts_example)
    await repo.upsert_question(
        lexeme.id,
        qtype="meaning_mcq",
        prompt=prompt,
        options=options,
        correct_option_id=correct,
        explanation=expl,
        status="approved",
        sense_id=sense.id,
        generator_meta={"source": "enrich_pack"},
    )
    cp, copts, cc, ce = _cloze_mcq(lexeme.display_word, ielts_example)
    q2 = await repo.upsert_question(
        lexeme.id,
        qtype="cloze",
        prompt=cp,
        options=copts,
        correct_option_id=cc,
        explanation=ce,
        status="generated",
        sense_id=sense.id,
    )
    ok, _ = validate_question(q2)
    q2.status = "approved" if ok else "validated"
    lexeme.status = "approved"
    await repo.s.flush()


async def enrich_pack_fast(repo: VocabLexiconRepo, pack_id: str) -> int:
    """Fast path: transipy VI gloss + template prompts/MCQ (no dictionary, no LLM)."""
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack: {pack_id}")

    target_band = float(spec.get("target_band_min", 6.0))
    pack_family = spec.get("pack_family") or "band"

    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == pack_id)
        .options(selectinload(VocabPackItem.lexeme))
        .order_by(VocabPackItem.order_index)
    )
    items = [
        item for item in (await repo.s.execute(stmt)).scalars().all() if item.lexeme
    ]
    if not items:
        return 0

    translator = get_translate_client()
    queries = [_gloss_translate_query(item.lexeme) for item in items]
    glosses: List[str]
    try:
        glosses = await translator.translate_batch(
            queries, target_language="vi", source_language="en"
        )
    except Exception as exc:
        logger.warning("transipy batch failed, using fallback glosses: {}", exc)
        glosses = [f"(nghĩa của {item.lexeme.display_word})" for item in items]

    if len(glosses) != len(items):
        glosses = (
            glosses + [f"(nghĩa của {item.lexeme.display_word})" for item in items]
        )[: len(items)]

    logger.info("Upserting senses/questions for {} words in {}", len(items), pack_id)
    count = 0
    for item, vi_gloss in zip(items, glosses):
        lexeme = item.lexeme
        labels = list(item.stat_labels or []) if hasattr(item, "stat_labels") else []
        vi_context = _vi_context_from_labels(labels)
        translate_prompt = _vi_translate_prompt(
            f"Nhiều người cho rằng {vi_context} rất quan trọng", lexeme.display_word
        )
        topic_prompt = _topic_prompt(vi_context, lexeme.display_word)
        example = _fast_example(lexeme, pack_family, vi_context)
        await _upsert_sense_and_questions(
            repo,
            lexeme,
            definition_en=_fast_definition_en(lexeme, pack_family),
            vi_gloss=vi_gloss.strip() or f"(nghĩa của {lexeme.display_word})",
            translate_prompt=translate_prompt,
            topic_prompt=topic_prompt,
            ielts_example=example,
            usage_note=(
                "Typical of formal academic English."
                if pack_family == "gre"
                else "Use naturally in IELTS — listening, reading, speaking, or writing."
            ),
            stat_labels=labels,
            pack_family=pack_family,
            target_band=target_band,
        )
        count += 1

    logger.info("Fast-enriched {} lexemes in pack {} (transipy)", count, pack_id)
    return count


async def _load_primary_sense(
    repo: VocabLexiconRepo, lexeme_id
) -> Optional[VocabSense]:
    stmt = select(VocabSense).where(
        VocabSense.lexeme_id == lexeme_id,
        VocabSense.sense_order == 1,
    )
    return (await repo.s.execute(stmt)).scalar_one_or_none()


async def enrich_pack_gloss_only(repo: VocabLexiconRepo, pack_id: str) -> int:
    """Transipy vi_gloss + template prompts only; keeps dictionary definition/example."""
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack: {pack_id}")

    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == pack_id)
        .options(selectinload(VocabPackItem.lexeme))
        .order_by(VocabPackItem.order_index)
    )
    items = [
        item for item in (await repo.s.execute(stmt)).scalars().all() if item.lexeme
    ]
    if not items:
        return 0

    # Only gloss words that already have a real definition (post backfill).
    eligible: List[tuple[VocabPackItem, VocabLexeme]] = []
    for item in items:
        sense = await _load_primary_sense(repo, item.lexeme.id)
        if not sense or not (sense.definition_en or "").strip():
            continue
        if sense.definition_en.strip() == item.lexeme.display_word:
            continue
        eligible.append((item, item.lexeme))

    if not eligible:
        logger.warning(
            "Pack {} — no lexemes with dictionary definitions for gloss-only", pack_id
        )
        return 0

    translator = get_translate_client()
    queries = [_gloss_translate_query(lex) for _, lex in eligible]
    try:
        glosses = await translator.translate_batch(
            queries, target_language="vi", source_language="en"
        )
    except Exception as exc:
        logger.warning("transipy batch failed for {}: {}", pack_id, exc)
        glosses = [f"(nghĩa của {lex.display_word})" for _, lex in eligible]

    if len(glosses) != len(eligible):
        glosses = (
            glosses + [f"(nghĩa của {lex.display_word})" for _, lex in eligible]
        )[: len(eligible)]

    count = 0
    skipped = 0
    for (item, lexeme), vi_gloss in zip(eligible, glosses):
        labels = list(item.stat_labels or []) if hasattr(item, "stat_labels") else []
        vi_context = _vi_context_from_labels(labels)
        translate_prompt = _vi_translate_prompt(
            f"Nhiều người cho rằng {vi_context} rất quan trọng", lexeme.display_word
        )
        topic_prompt = _topic_prompt(vi_context, lexeme.display_word)
        sense = await repo.patch_primary_sense_gloss(
            lexeme.id,
            vi_gloss=vi_gloss.strip() or f"(nghĩa của {lexeme.display_word})",
            vi_translate_prompt=translate_prompt,
            topic_prompt=topic_prompt,
        )
        if sense:
            count += 1
            if lexeme.status == "draft":
                lexeme.status = "enriched"
        else:
            skipped += 1

    logger.info(
        "Gloss-only: {} vi_gloss updated in {} ({} skipped, {} not eligible)",
        count,
        pack_id,
        skipped,
        len(items) - len(eligible),
    )
    return count


async def enrich_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    fast: bool = False,
    gloss_only: bool = False,
) -> int:
    if gloss_only:
        return await enrich_pack_gloss_only(repo, pack_id)
    if fast:
        return await enrich_pack_fast(repo, pack_id)

    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack: {pack_id}")

    target_band = float(spec.get("target_band_min", 6.0))
    pack_family = spec.get("pack_family") or "band"

    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == pack_id)
        .options(selectinload(VocabPackItem.lexeme))
        .order_by(VocabPackItem.order_index)
    )
    items = (await repo.s.execute(stmt)).scalars().all()
    count = 0
    for item in items:
        if not item.lexeme:
            continue
        labels = list(item.stat_labels or []) if hasattr(item, "stat_labels") else []
        await enrich_lexeme_in_pack(
            repo,
            item.lexeme,
            stat_labels=labels,
            target_band=target_band,
            pack_family=pack_family,
        )
        count += 1
        await asyncio.sleep(0.15)
    logger.info("Enriched {} lexemes in pack {}", count, pack_id)
    return count
