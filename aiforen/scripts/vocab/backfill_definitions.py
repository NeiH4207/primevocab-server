"""Backfill definition, example, phonetic, audio from dictionaryapi.dev (batched + progress logs)."""

from __future__ import annotations

import os

# Host CLI: .env CORS_ORIGINS is comma-separated; Settings expects JSON list.
os.environ["CORS_ORIGINS"] = '["http://localhost:3000","http://127.0.0.1:3000"]'
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

import argparse
import asyncio
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabLexeme, VocabPack, VocabPackItem, VocabSense
from aiforen.integrations.dictionary.free_dictionary import (
    fetch_entry,
    pick_best_definition,
    pick_definition,
    pick_phonetic_audio,
)

MIN_USABLE_DEFINITION_LEN = 20


async def _llm_definition(word: str, pos: str, *, pack_family: str = "band") -> str:
    """Dictionary-style definition when dictionary API only returns stubs."""
    import json

    from openai import AsyncOpenAI

    from aiforen.core.config import get_settings

    client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    level = (
        "GRE / graduate-level academic English (precise, formal register)"
        if pack_family == "gre"
        else "IELTS academic English"
    )
    prompt = (
        f"Write one clear English dictionary definition for the word '{word}' "
        f"as {pos} ({level}, 40-55 words). Return ONLY the definition text, no quotes."
    )
    resp = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("{"):
        data = json.loads(text)
        text = data.get(word.lower()) or data.get(word) or text
    return text.strip()


from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.build_packs import BAND_PACKS
from aiforen.scripts.vocab.oxford_packs import OXFORD_PACKS
from aiforen.scripts.vocab.pack_specs import get_pack_spec

# dictionaryapi.dev returns 404 for some high-frequency NGSL lemmas
MANUAL_FALLBACKS: dict[str, tuple[str, str, str]] = {
    "every": (
        "Used to refer to all the individual members of a group, with no exceptions.",
        "Every student must submit the essay before the deadline.",
        "/ˈevri/",
    ),
    "look": (
        "To direct your eyes toward someone or something in order to see.",
        "Look at the chart on the screen before you answer the question.",
        "/lʊk/",
    ),
    "touch": (
        "To put your hand or fingers on something; to come into contact with.",
        "Please do not touch the exhibits in the museum.",
        "/tʌtʃ/",
    ),
    "net": (
        "A material made of threads with open spaces; also the amount remaining after deductions.",
        "The net effect of the reform was a steady fall in unemployment.",
        "/net/",
    ),
    "phase": (
        "To introduce or carry out something in gradual stages; a distinct stage in a process.",
        "The city will phase in the new recycling rules over six months.",
        "/feɪz/",
    ),
    "hell": (
        "In religion, the place of punishment after death; informally, a very unpleasant experience.",
        "Aid workers described conditions in the camp as hell on earth.",
        "/hel/",
    ),
    "tight": (
        "Fixed firmly in place; with little space or money; strict or demanding.",
        "Many graduates face a tight job market in their first year.",
        "/taɪt/",
    ),
    # GRE — dictionaryapi.dev 404
    "accumulation": (
        "The gradual gathering or increase of something over time.",
        "The passage argues that an accumulation of small reforms can reshape an institution.",
        "/əˌkjuːmjəˈleɪʃn/",
    ),
    "bleed": (
        "To lose blood; to spread slowly beyond a boundary (color, meaning, or effect).",
        "In the analogy, one idea bleeds into the next without a clear transition.",
        "/bliːd/",
    ),
    "cue": (
        "A signal that tells someone to act; a hint that suggests what comes next.",
        "The author uses the opening sentence as a cue that the essay will challenge the theory.",
        "/kjuː/",
    ),
    "dose": (
        "A measured amount of medicine; figuratively, an amount of something delivered at one time.",
        "A large dose of irony runs through the critic's review of the policy.",
        "/doʊs/",
    ),
    "phonological": (
        "Relating to the system of speech sounds in a language.",
        "The study examines phonological patterns that distinguish similar word forms.",
        "/ˌfoʊnəˈlɒdʒɪkəl/",
    ),
    "subset": (
        "A set whose members all belong to a larger set; a smaller group within a group.",
        "Only a subset of the participants showed a statistically significant response.",
        "/ˈsʌbset/",
    ),
    "suicide": (
        "The act of intentionally causing one's own death.",
        "The report discusses suicide rates as a public-health indicator, not as sensational detail.",
        "/ˈsuːɪsaɪd/",
    ),
    "syntactic": (
        "Relating to the arrangement of words and phrases to form grammatical sentences.",
        "A syntactic ambiguity in the stem makes two answer choices seem equally plausible.",
        "/sɪnˈtæktɪk/",
    ),
    "variability": (
        "The quality of being changeable or inconsistent; degree of variation.",
        "High variability in the data weakens the author's claim of a stable trend.",
        "/ˌveriəˈbɪləti/",
    ),
}


def _is_placeholder_definition(
    defn: str, lexeme: VocabLexeme, pack_family: str
) -> bool:
    if not defn or defn.strip() == lexeme.display_word:
        return True
    pos = lexeme.pos or "word"
    if defn == f"A common {pos} used in IELTS academic English.":
        return True
    if defn == f"A formal {pos} common on GRE Verbal.":
        return True
    return False


# NGSL/NAWL crawl stores shorthand like "park n., v." — not a real dictionary definition.
_NGSL_SHORTHAND = (
    r"^[a-zA-Z''-]+ (n\.|v\.|adj\.|adv\.|prep\.|conj\.|pron\.|interj\.)"
    r"(, (n\.|v\.|adj\.|adv\.|prep\.|conj\.|pron\.|interj\.))*$"
)


def _is_ngsl_shorthand_definition(defn: str) -> bool:
    return bool(defn and re.match(_NGSL_SHORTHAND, defn.strip()))


def _has_usable_definition(
    defn: Optional[str], *, min_len: int = MIN_USABLE_DEFINITION_LEN
) -> bool:
    text = (defn or "").strip()
    return len(text) >= min_len


def _has_usable_example(sense: Optional[VocabSense], pack_family: str) -> bool:
    if not sense:
        return False
    ex = sense.gre_example if pack_family == "gre" else sense.ielts_example
    if not ex and pack_family != "gre":
        ex = sense.ielts_example or sense.gre_example
    if not ex or _is_template_example(ex):
        return False
    min_len = 20 if pack_family in ("band", "ielts") else 15
    return len(ex.strip()) > min_len


def _has_usable_phonetic(sense: Optional[VocabSense]) -> bool:
    return bool(sense and sense.phonetic and len(sense.phonetic.strip()) > 2)


def _is_template_example(example: str) -> bool:
    low = example.lower()
    if "the passage uses" in low and "sharpen" in low:
        return True
    if "many people say" in low and "appreciation" in low:
        return True
    return False


def _has_dictionary_example(
    sense: Optional[VocabSense], lexeme: VocabLexeme, pack_family: str
) -> bool:
    """True when example looks like a real dictionary sentence (not book paste / template)."""
    if not sense:
        return False
    ex = (sense.ielts_example or sense.gre_example or "").strip()
    if not ex:
        return False
    defn = (sense.definition_en or "").strip()
    if ex == defn:
        return False
    if _is_ngsl_shorthand_definition(ex):
        return False
    if _is_template_example(ex):
        return False
    return True


def _needs_example_ipa_backfill(
    existing: Optional[VocabSense],
    pack_family: str,
    lexeme: Optional[VocabLexeme] = None,
) -> bool:
    """True when primary sense is missing example and/or IPA."""
    if not existing:
        return True
    if pack_family == "cefr" and lexeme:
        return not _has_dictionary_example(
            existing, lexeme, pack_family
        ) or not _has_usable_phonetic(existing)
    return not _has_usable_example(existing, pack_family) or not _has_usable_phonetic(
        existing
    )


def _should_skip_backfill(
    existing: Optional[VocabSense],
    lexeme: VocabLexeme,
    *,
    pack_family: str,
    force: bool,
) -> bool:
    """Skip API only when content already looks complete."""
    if force or not existing:
        return False
    if pack_family == "cefr":
        return _has_dictionary_example(
            existing, lexeme, pack_family
        ) and _has_usable_phonetic(existing)
    defn = (existing.definition_en or "").strip()
    if _is_placeholder_definition(defn, lexeme, pack_family):
        return False
    if _is_ngsl_shorthand_definition(defn):
        return False
    if not _has_usable_definition(defn):
        return False
    if not _has_usable_example(existing, pack_family):
        return False
    return True


@dataclass
class BatchStats:
    ok: int = 0
    miss: int = 0
    skip: int = 0
    err: int = 0


async def _load_primary_sense(
    repo: VocabLexiconRepo, lexeme_id
) -> Optional[VocabSense]:
    stmt = select(VocabSense).where(
        VocabSense.lexeme_id == lexeme_id,
        VocabSense.sense_order == 1,
    )
    return (await repo.s.execute(stmt)).scalar_one_or_none()


def _primary_sense_from_item(item: VocabPackItem) -> Optional[VocabSense]:
    if not item.lexeme or not item.lexeme.senses:
        return None
    for sense in item.lexeme.senses:
        if sense.sense_order == 1:
            return sense
    return None


def _pack_items_needing_short_definition(
    items: List[VocabPackItem],
) -> List[VocabPackItem]:
    out: List[VocabPackItem] = []
    for item in items:
        if not item.lexeme:
            continue
        sense = _primary_sense_from_item(item)
        defn = sense.definition_en if sense else None
        if not _has_usable_definition(defn):
            out.append(item)
    return out


def _pack_items_needing_example_ipa(
    items: List[VocabPackItem], pack_family: str
) -> List[VocabPackItem]:
    """In-memory filter: only words missing example and/or IPA (no API scan of full pack)."""
    return [
        item
        for item in items
        if item.lexeme
        and _needs_example_ipa_backfill(
            _primary_sense_from_item(item), pack_family, item.lexeme
        )
    ]


async def _backfill_one(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    pack_family: str,
    force: bool,
) -> str:
    existing = await _load_primary_sense(repo, lexeme.id)
    if _should_skip_backfill(existing, lexeme, pack_family=pack_family, force=force):
        return "skip"

    preserve_book = pack_family == "cefr" and existing is not None
    word_key = lexeme.display_word.lower()
    manual = MANUAL_FALLBACKS.get(word_key)

    entry = await fetch_entry(lexeme.display_word)
    if not entry:
        if manual:
            definition, example_val, phonetic = manual
            if preserve_book:
                definition = existing.definition_en
            ielts_ex = (
                example_val
                if pack_family != "gre"
                else (existing.ielts_example if existing else None)
            )
            gre_ex = example_val if pack_family == "gre" else None
            await repo.upsert_primary_sense(
                lexeme.id,
                definition_en=definition,
                vi_gloss=existing.vi_gloss if existing else None,
                vi_translate_prompt=existing.vi_translate_prompt if existing else None,
                topic_prompt=existing.topic_prompt if existing else None,
                usage_note=existing.usage_note if existing else None,
                ielts_example=ielts_ex,
                gre_example=gre_ex,
                phonetic=phonetic,
                audio_url=existing.audio_url if existing else None,
                topic_tags=(
                    list(existing.topic_tags)
                    if existing and existing.topic_tags
                    else [lexeme.lemma]
                ),
                tips=(
                    existing.tips
                    if existing and isinstance(existing.tips, list)
                    else []
                ),
            )
            if lexeme.status not in ("approved", "deprecated"):
                lexeme.status = "enriched"
            return "ok"
        if not existing:
            await repo.upsert_primary_sense(
                lexeme.id,
                definition_en=lexeme.display_word,
                vi_gloss=None,
                ielts_example=None,
            )
        return "miss"

    definition, example, _ = pick_definition(entry, lexeme.pos)
    if not definition:
        definition = lexeme.display_word
    if preserve_book:
        definition = existing.definition_en

    phonetic, audio_url = pick_phonetic_audio(entry)
    if preserve_book and existing.phonetic and not phonetic:
        phonetic = existing.phonetic
        audio_url = existing.audio_url or audio_url

    need_ex = (
        not _has_dictionary_example(existing, lexeme, pack_family)
        if preserve_book
        else True
    )
    example_val = example or (existing.ielts_example if existing else None)
    if need_ex and not example_val:
        w = lexeme.display_word
        if pack_family == "gre":
            example_val = f"The passage uses {w} to sharpen the author's claim."
        elif pack_family == "cefr":
            example_val = f"Learners often encounter {w} in everyday English texts."
        else:
            example_val = f"Many people say {w} when they want to show appreciation."
        if w.lower() == "thank":
            example_val = "She said thank you for all your help during the project."
    elif preserve_book and not need_ex:
        example_val = existing.ielts_example
    gre_ex = example_val if pack_family == "gre" else None
    ielts_ex = (
        example_val
        if pack_family != "gre"
        else (existing.ielts_example if existing else None)
    )

    await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=definition,
        vi_gloss=existing.vi_gloss if existing else None,
        vi_translate_prompt=existing.vi_translate_prompt if existing else None,
        topic_prompt=existing.topic_prompt if existing else None,
        usage_note=existing.usage_note if existing else None,
        ielts_example=ielts_ex,
        gre_example=gre_ex,
        phonetic=phonetic,
        audio_url=audio_url,
        topic_tags=(
            list(existing.topic_tags)
            if existing and existing.topic_tags
            else [lexeme.lemma]
        ),
        tips=existing.tips if existing and isinstance(existing.tips, list) else [],
    )
    if lexeme.status not in ("approved", "deprecated"):
        lexeme.status = "enriched"
    return "ok"


def _example_for_pack(
    lexeme: VocabLexeme,
    pack_family: str,
    *,
    api_example: str,
    existing: Optional[VocabSense],
) -> tuple[Optional[str], Optional[str]]:
    """Return (ielts_example, gre_example) values to persist (may keep existing)."""
    need_ex = not _has_usable_example(existing, pack_family) if existing else True
    if not need_ex:
        return existing.ielts_example if existing else None, (
            existing.gre_example if existing else None
        )

    example_val = api_example or (existing.ielts_example if existing else None)
    if not example_val and existing:
        example_val = existing.gre_example
    if not example_val:
        w = lexeme.display_word
        if pack_family == "gre":
            example_val = f"The passage uses {w} to sharpen the author's claim."
        else:
            example_val = f"Many people say {w} when they want to show appreciation."
        if w.lower() == "thank":
            example_val = "She said thank you for all your help during the project."

    if pack_family == "gre":
        return (
            existing.ielts_example if existing else None,
            example_val,
        )
    return (example_val, existing.gre_example if existing else None)


async def _backfill_short_definition_one(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    pack_family: str,
) -> str:
    """Replace stub definitions (<20 chars); keep good example/IPA and Step3 fields."""
    existing = await _load_primary_sense(repo, lexeme.id)
    if existing and _has_usable_definition(existing.definition_en):
        return "skip"

    word_key = lexeme.display_word.lower()
    manual = MANUAL_FALLBACKS.get(word_key)
    definition = (existing.definition_en if existing else None) or ""
    ielts_ex = existing.ielts_example if existing else None
    gre_ex = existing.gre_example if existing else None
    phonetic = existing.phonetic if existing else None
    audio_url = existing.audio_url if existing else None

    entry = await fetch_entry(lexeme.display_word)
    if entry:
        definition, api_ex, _ = pick_best_definition(
            entry, lexeme.pos, min_len=MIN_USABLE_DEFINITION_LEN
        )
        if api_ex and not _has_usable_example(existing, pack_family):
            ielts_ex, gre_ex = _example_for_pack(
                lexeme, pack_family, api_example=api_ex, existing=existing
            )
        picked_phonetic, picked_audio = pick_phonetic_audio(entry)
        if picked_phonetic and not _has_usable_phonetic(existing):
            phonetic = picked_phonetic
        if picked_audio:
            audio_url = picked_audio or audio_url

    if manual and not _has_usable_definition(definition):
        man_defn, man_ex, man_ipa = manual
        definition = man_defn
        if not _has_usable_example(existing, pack_family):
            ielts_ex, gre_ex = _example_for_pack(
                lexeme, pack_family, api_example=man_ex, existing=existing
            )
        if not _has_usable_phonetic(existing):
            phonetic = man_ipa

    if not _has_usable_definition(definition):
        try:
            definition = await _llm_definition(
                lexeme.display_word,
                lexeme.pos or "word",
                pack_family=pack_family,
            )
        except Exception as exc:
            logger.warning("LLM definition failed for {}: {}", lexeme.display_word, exc)

    if not _has_usable_definition(definition):
        return "miss"

    await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=definition.strip(),
        vi_gloss=existing.vi_gloss if existing else None,
        vi_translate_prompt=existing.vi_translate_prompt if existing else None,
        topic_prompt=existing.topic_prompt if existing else None,
        usage_note=existing.usage_note if existing else None,
        ielts_example=ielts_ex,
        gre_example=gre_ex,
        phonetic=phonetic,
        audio_url=audio_url,
        topic_tags=(
            list(existing.topic_tags)
            if existing and existing.topic_tags
            else [lexeme.lemma]
        ),
        tips=existing.tips if existing and isinstance(existing.tips, list) else [],
    )
    if lexeme.status not in ("approved", "deprecated"):
        lexeme.status = "enriched"

    updated = await _load_primary_sense(repo, lexeme.id)
    if not _has_usable_definition(updated.definition_en if updated else None):
        return "miss"
    return "ok"


async def _backfill_example_ipa_one(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    pack_family: str,
) -> str:
    """Fill missing example / IPA only; keep definition_en and vi fields."""
    existing = await _load_primary_sense(repo, lexeme.id)
    if not _needs_example_ipa_backfill(existing, pack_family, lexeme):
        return "skip"
    if not existing:
        return "miss"

    preserve_book = pack_family == "cefr"
    need_ex = (
        not _has_dictionary_example(existing, lexeme, pack_family)
        if preserve_book
        else not _has_usable_example(existing, pack_family)
    )
    need_ipa = not _has_usable_phonetic(existing)

    word_key = lexeme.display_word.lower()
    manual = MANUAL_FALLBACKS.get(word_key)
    api_example = ""
    phonetic = existing.phonetic
    audio_url = existing.audio_url

    entry = await fetch_entry(lexeme.display_word)
    if entry:
        _, api_example, _ = pick_definition(entry, lexeme.pos)
        picked_phonetic, picked_audio = pick_phonetic_audio(entry)
        if need_ipa and picked_phonetic:
            phonetic = picked_phonetic
            audio_url = picked_audio
    elif manual and (need_ex or need_ipa):
        _defn, manual_ex, manual_ipa = manual
        if need_ex:
            api_example = manual_ex
        if need_ipa:
            phonetic = manual_ipa
    elif need_ex or need_ipa:
        return "miss"

    if preserve_book and not need_ex:
        ielts_ex = existing.ielts_example
        gre_ex = existing.gre_example
    else:
        ielts_ex, gre_ex = _example_for_pack(
            lexeme, pack_family, api_example=api_example, existing=existing
        )

    await repo.upsert_primary_sense(
        lexeme.id,
        definition_en=existing.definition_en,
        vi_gloss=existing.vi_gloss,
        vi_translate_prompt=existing.vi_translate_prompt,
        topic_prompt=existing.topic_prompt,
        usage_note=existing.usage_note,
        ielts_example=ielts_ex,
        gre_example=gre_ex,
        phonetic=phonetic,
        audio_url=audio_url,
        topic_tags=list(existing.topic_tags) if existing.topic_tags else [lexeme.lemma],
        tips=list(existing.tips) if isinstance(existing.tips, list) else [],
    )
    if lexeme.status not in ("approved", "deprecated"):
        lexeme.status = "enriched"
    updated = await _load_primary_sense(repo, lexeme.id)
    if _needs_example_ipa_backfill(updated, pack_family, lexeme):
        return "miss"
    return "ok"


async def backfill_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    batch_size: int,
    sleep_s: float,
    force: bool,
    gaps_only: bool = False,
    short_defs_only: bool = False,
) -> BatchStats:
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack: {pack_id}")

    pack_family = spec.get("pack_family") or "band"
    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == pack_id)
        .options(selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.senses))
        .order_by(VocabPackItem.order_index)
    )
    items = [i for i in (await repo.s.execute(stmt)).scalars().all() if i.lexeme]
    pack_total = len(items)
    if short_defs_only:
        work_items = _pack_items_needing_short_definition(items)
    elif gaps_only or pack_family == "cefr":
        work_items = _pack_items_needing_example_ipa(items, pack_family)
    else:
        work_items = items
    total = len(work_items)
    totals = BatchStats()

    mode = "short definitions (<20 chars)" if short_defs_only else "definitions"
    if gaps_only:
        mode = "example/IPA gaps"
    if pack_family == "cefr" and not gaps_only:
        mode = "CEFR example/IPA (dictionaryapi.dev, keep book def/VI)"
    if gaps_only or short_defs_only:
        logger.info(
            "Pack {} — start backfill {} ({} gaps / {} pack words, batch={}, sleep={}s)",
            pack_id,
            mode,
            total,
            pack_total,
            batch_size,
            sleep_s,
        )
        if total == 0:
            logger.info("Pack {} — no {} gaps; nothing to do", pack_id, mode)
            return totals
    else:
        logger.info(
            "Pack {} — start backfill {} ({} words, batch={}, sleep={}s, force={})",
            pack_id,
            mode,
            total,
            batch_size,
            sleep_s,
            force,
        )

    for batch_start in range(0, total, batch_size):
        batch = work_items[batch_start : batch_start + batch_size]
        batch_stats = BatchStats()
        t0 = time.perf_counter()

        for item in batch:
            result = "err"
            try:
                if gaps_only:
                    result = await _backfill_example_ipa_one(
                        repo, item.lexeme, pack_family=pack_family
                    )
                elif short_defs_only:
                    result = await _backfill_short_definition_one(
                        repo, item.lexeme, pack_family=pack_family
                    )
                else:
                    result = await _backfill_one(
                        repo, item.lexeme, pack_family=pack_family, force=force
                    )
                setattr(batch_stats, result, getattr(batch_stats, result) + 1)
                totals_ok = getattr(totals, result)
                setattr(totals, result, totals_ok + 1)
                if sleep_s > 0 and (
                    not gaps_only and not short_defs_only or result in ("ok", "miss")
                ):
                    await asyncio.sleep(sleep_s)
            except Exception as exc:
                await repo.s.rollback()
                batch_stats.err += 1
                totals.err += 1
                logger.warning("Backfill error {}: {}", item.lexeme.display_word, exc)

        await repo.s.commit()  # commit per batch so progress persists and locks release
        done = min(batch_start + len(batch), total)
        elapsed = time.perf_counter() - t0
        logger.info(
            "Pack {} — batch {}/{} | progress {}/{} ({:.1f}%) | "
            "batch ok={} miss={} skip={} err={} | {:.1f}s | "
            "total ok={} miss={} skip={} err={}",
            pack_id,
            batch_start // batch_size + 1,
            (total + batch_size - 1) // batch_size,
            done,
            total,
            100.0 * done / total if total else 100.0,
            batch_stats.ok,
            batch_stats.miss,
            batch_stats.skip,
            batch_stats.err,
            elapsed,
            totals.ok,
            totals.miss,
            totals.skip,
            totals.err,
        )

    pack_row = (
        await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
    ).scalar_one_or_none()
    if pack_row:
        pack_row.completed_word_count = totals.ok + totals.miss
        await repo.s.flush()
    await repo.s.commit()

    logger.info(
        "Pack {} — done | ok={} miss={} skip={} err={} / {}{}",
        pack_id,
        totals.ok,
        totals.miss,
        totals.skip,
        totals.err,
        total,
        f" (pack size {pack_total})" if gaps_only else "",
    )
    return totals


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill dict definitions (dictionaryapi.dev)"
    )
    parser.add_argument("pack_id", nargs="?", help="e.g. pack_band_6")
    parser.add_argument(
        "--all", action="store_true", help="All active band + GRE packs"
    )
    parser.add_argument(
        "--bands-only",
        action="store_true",
        help="Only IELTS pack_band_4 … pack_band_9 (exclude GRE)",
    )
    parser.add_argument(
        "--oxford-only",
        action="store_true",
        help="Oxford CEFR packs: API example + IPA; keep book definition_en and vi_gloss",
    )
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help="Fill missing example and/or IPA only; do not change definition_en",
    )
    parser.add_argument(
        "--short-defs-only",
        action="store_true",
        help=f"Re-fetch definition_en when shorter than {MIN_USABLE_DEFINITION_LEN} chars",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Words per progress log batch"
    )
    parser.add_argument(
        "--sleep", type=float, default=0.25, help="Delay between API calls (seconds)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch API even when definition and example already look complete",
    )
    args = parser.parse_args()

    if args.gaps_only and args.force:
        parser.error("--gaps-only cannot be used with --force")
    if args.gaps_only and args.short_defs_only:
        parser.error("--gaps-only cannot be used with --short-defs-only")

    pack_ids: List[str]
    if args.all:
        pack_ids = [s["pack_id"] for s in BAND_PACKS]
    elif args.bands_only:
        pack_ids = [s["pack_id"] for s in BAND_PACKS if s["pack_id"] != "pack_gre"]
    elif args.oxford_only:
        pack_ids = [s["pack_id"] for s in OXFORD_PACKS]
    elif args.pack_id:
        pack_ids = [args.pack_id]
    else:
        parser.print_help()
        return

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()

    grand = BatchStats()
    for pack_id in pack_ids:
        async with sm() as session:
            repo = VocabLexiconRepo(session)
            stats = await backfill_pack(
                repo,
                pack_id,
                batch_size=max(1, args.batch_size),
                sleep_s=max(0.0, args.sleep),
                force=args.force,
                gaps_only=args.gaps_only,
                short_defs_only=args.short_defs_only,
            )
        grand.ok += stats.ok
        grand.miss += stats.miss
        grand.skip += stats.skip
        grand.err += stats.err

    logger.info(
        "All packs finished | ok={} miss={} skip={} err={}",
        grand.ok,
        grand.miss,
        grand.skip,
        grand.err,
    )


if __name__ == "__main__":
    asyncio.run(main())
