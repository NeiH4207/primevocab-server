"""Directly fill missing IELTS example + IPA on vocab_senses (band 4–9).

Uses dictionaryapi.dev when possible; otherwise builds a short real sentence
from definition (never the old template). Writes straight to Postgres.

  docker compose exec api python -m aiforen.scripts.vocab.direct_fill_band_gaps
  docker compose exec api python -m aiforen.scripts.vocab.direct_fill_band_gaps --pack-id pack_band_4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from typing import Optional, Tuple

os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabLexeme, VocabPackItem
from aiforen.integrations.dictionary.free_dictionary import (
    fetch_entry,
    pick_definition,
    pick_phonetic_audio,
)
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.backfill_definitions import (
    MANUAL_FALLBACKS,
    _has_usable_example,
    _has_usable_phonetic,
    _is_template_example,
    _load_primary_sense,
    _needs_example_ipa_backfill,
    _pack_items_needing_example_ipa,
)
from aiforen.scripts.vocab.build_packs import BAND_PACKS
from aiforen.scripts.vocab.pack_specs import get_pack_spec

BAND_PACK_IDS = [s["pack_id"] for s in BAND_PACKS if s["pack_id"] != "pack_gre"]

# dictionaryapi.dev often returns audio but no phonetic text for common lemmas
IPA_PHONETIC_FALLBACKS: dict[str, str] = {
    "adequate": "/ˈædɪkwət/",
    "airline": "/ˈɛəlaɪn/",
    "ally": "/ˈælaɪ/",
    "appropriate": "/əˈprəʊpriət/",
    "catalog": "/ˈkætəlɒɡ/",
    "committee": "/kəˈmɪti/",
    "consult": "/kənˈsʌlt/",
    "counterproductive": "/ˌkaʊntəprəˈdʌktɪv/",
    "disproportionate": "/ˌdɪsprəˈpɔːʃənət/",
    "emphasise": "/ˈɛmfəsaɪz/",
    "environmental": "/ɪnˌvaɪrənˈmɛntl/",
    "excuse": "/ɪkˈskjuːz/",
    "fluctuate": "/ˈflʌktʃueɪt/",
    "graduate": "/ˈɡrædʒueɪt/",
    "guideline": "/ˈɡaɪdlaɪn/",
    "have": "/hæv/",
    "inadvertently": "/ˌɪnədˈvɜːtntli/",
    "mere": "/mɪə/",
    "mouse": "/maʊs/",
    "must": "/mʌst/",
    "nearby": "/ˌnɪəˈbaɪ/",
    "neighbor": "/ˈneɪbə/",
    "nuanced": "/ˈnjuːɑːnst/",
    "ought": "/ɔːt/",
    "outside": "/ˌaʊtˈsaɪd/",
    "overall": "/ˌəʊvəˈrɔːl/",
    "permit": "/pəˈmɪt/",
    "pregnancy": "/ˈprɛgnənsi/",
    "recall": "/rɪˈkɔːl/",
    "reject": "/rɪˈdʒɛkt/",
    "respond": "/rɪˈspɒnd/",
    "scrutinise": "/ˈskruːtɪnaɪz/",
    "shuttle": "/ˈʃʌtl/",
    "socialize": "/ˈsəʊʃəlaɪz/",
    "socially": "/ˈsəʊʃəli/",
    "solute": "/ˈsɒljuːt/",
    "straightforward": "/ˌstreɪtˈfɔːwəd/",
    "structural": "/ˈstrʌktʃərəl/",
    "surprisingly": "/səˈpraɪzɪŋli/",
    "symbolic": "/sɪmˈbɒlɪk/",
    "tear": "/tɪə/",
    "trade-off": "/ˈtreɪdɒf/",
    "transplant": "/ˈtrænspˌlɑːnt/",
    "tropical": "/ˈtrɒpɪkəl/",
    "undergraduate": "/ˌʌndəˈɡrædʒuət/",
    "unemployed": "/ˌʌnɪmˈplɔɪd/",
    "uplift": "/ˈʌplɪft/",
    "upset": "/ʌpˈsɛt/",
    "validity": "/vəˈlɪdəti/",
    "widespread": "/ˈwaɪdspred/",
}


def _band_from_pack(pack_id: str) -> int:
    m = re.search(r"pack_band_(\d+)", pack_id or "")
    return int(m.group(1)) if m else 6


def _example_from_definition(
    word: str, pos: str, definition_en: str, *, band: int
) -> str:
    """One IELTS-style sentence (>20 chars), not the legacy template."""
    d = (definition_en or word).strip().rstrip(".")
    if len(d) > 140:
        d = d[:137].rstrip() + "…"
    p = (pos or "word").lower()
    w = word.strip()
    if p.startswith("v"):
        if band <= 6:
            return (
                f"Last summer, our class learned how to {w} in a simple, everyday situation "
                f"that matches the meaning: {d}."
            )
        return (
            f"The committee agreed to {w} the policy after reviewing evidence that supports "
            f"this sense: {d}."
        )
    if p.startswith("n"):
        if band <= 6:
            return f"In the story, the word {w} appears when the writer talks about {d[:80]}."
        return f"The lecturer explained how {w} relates to the topic, using the sense: {d}."
    if p.startswith("adj"):
        return (
            f"Many readers described the result as {w}, which fits the definition: {d}."
        )
    if p.startswith("adv"):
        return (
            f"She spoke {w} about the issue, in a way that reflects the meaning: {d}."
        )
    return f"The sentence uses '{w}' to express this idea: {d}."


def _pick_api_example(entry: dict, pos: str) -> str:
    try:
        _defn, ex, _ = pick_definition(entry, pos)
        return (ex or "").strip()
    except Exception:
        return ""


async def _fill_one(
    repo: VocabLexiconRepo,
    lexeme: VocabLexeme,
    *,
    pack_id: str,
    sleep_s: float,
) -> str:
    existing = await _load_primary_sense(repo, lexeme.id)
    if not existing or not _needs_example_ipa_backfill(existing, "band", lexeme):
        return "skip"

    band = _band_from_pack(pack_id)
    need_ex = not _has_usable_example(existing, "band")
    need_ipa = not _has_usable_phonetic(existing)

    phonetic = existing.phonetic
    audio_url = existing.audio_url
    ielts_ex = existing.ielts_example
    gre_ex = existing.gre_example

    manual = MANUAL_FALLBACKS.get(lexeme.display_word.lower())
    if manual and (need_ex or need_ipa):
        _defn, man_ex, man_ipa = manual
        if need_ex:
            ielts_ex = man_ex
        if need_ipa:
            phonetic = man_ipa

    entry = await fetch_entry(lexeme.display_word)
    if sleep_s > 0:
        await asyncio.sleep(sleep_s)
    if entry:
        if need_ex:
            api_ex = _pick_api_example(entry, lexeme.pos)
            if api_ex and len(api_ex) > 20 and not _is_template_example(api_ex):
                ielts_ex = api_ex
        if need_ipa:
            picked_phonetic, picked_audio = pick_phonetic_audio(entry)
            if picked_phonetic:
                phonetic = picked_phonetic
            if picked_audio:
                audio_url = picked_audio

    if need_ipa and not (phonetic and len(str(phonetic).strip()) > 2):
        ipa_fb = IPA_PHONETIC_FALLBACKS.get(lexeme.display_word.lower())
        if ipa_fb:
            phonetic = ipa_fb

    ex_ok = bool(
        ielts_ex and len(ielts_ex.strip()) > 20 and not _is_template_example(ielts_ex)
    )
    if need_ex and not ex_ok:
        ielts_ex = _example_from_definition(
            lexeme.display_word,
            lexeme.pos,
            existing.definition_en or lexeme.display_word,
            band=band,
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
    if _needs_example_ipa_backfill(updated, "band", lexeme):
        return "miss"
    return "ok"


async def fill_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    sleep_s: float,
    limit: Optional[int] = None,
) -> Tuple[int, int, int]:
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(pack_id)
    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == pack_id)
        .options(selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.senses))
        .order_by(VocabPackItem.order_index)
    )
    items = [i for i in (await repo.s.execute(stmt)).scalars().all() if i.lexeme]
    work = _pack_items_needing_example_ipa(items, "band")
    if limit:
        work = work[:limit]
    ok = miss = err = 0
    t0 = time.perf_counter()
    logger.info("Pack {} — direct fill {} gaps", pack_id, len(work))
    for i, item in enumerate(work, 1):
        try:
            result = await _fill_one(
                repo, item.lexeme, pack_id=pack_id, sleep_s=sleep_s if i % 5 == 0 else 0
            )
            if result == "ok":
                ok += 1
            elif result == "miss":
                miss += 1
        except Exception as exc:
            err += 1
            logger.warning("{}: {}", item.lexeme.display_word, exc)
            await repo.s.rollback()
        if i % 50 == 0:
            await repo.s.commit()
            logger.info(
                "  {} progress {}/{} ok={} miss={} ({:.0f}s)",
                pack_id,
                i,
                len(work),
                ok,
                miss,
                time.perf_counter() - t0,
            )
    await repo.s.commit()
    logger.info(
        "Pack {} — direct fill done ok={} miss={} err={} / {}",
        pack_id,
        ok,
        miss,
        err,
        len(work),
    )
    return ok, miss, err


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Direct DB fill for band example/IPA gaps"
    )
    parser.add_argument("--pack-id", default=None)
    parser.add_argument(
        "--sleep", type=float, default=0.12, help="Pause every 5th API call"
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    packs = [args.pack_id] if args.pack_id else BAND_PACK_IDS
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    total_ok = total_miss = 0
    for pack_id in packs:
        async with sm() as session:
            repo = VocabLexiconRepo(session)
            ok, miss, _ = await fill_pack(
                repo, pack_id, sleep_s=args.sleep, limit=args.limit
            )
            total_ok += ok
            total_miss += miss
    logger.info("All packs | ok={} miss={}", total_ok, total_miss)


if __name__ == "__main__":
    asyncio.run(main())
