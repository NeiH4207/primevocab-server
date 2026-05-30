"""Fill remaining GRE pack example / IPA gaps (API + typo aliases + manual fallbacks)."""

from __future__ import annotations

import argparse
import asyncio
import os

os.environ["CORS_ORIGINS"] = '["http://localhost:3000","http://127.0.0.1:3000"]'
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
    _example_for_pack,
    _has_usable_example,
    _has_usable_phonetic,
    _needs_example_ipa_backfill,
    _primary_sense_from_item,
)

# dictionaryapi.dev lookup aliases for typos / phrases in the GRE list
GRE_API_ALIASES: dict[str, list[str]] = {
    "abreast of": ["abreast"],
    "destitude": ["destitute"],
    "desultority": ["desultory"],
    "habour": ["harbour", "harbor"],
    "halcygon": ["halcyon"],
    "holi-polloi": ["hoi polloi"],
    "quitoxic": ["quixotic"],
    "run the gaunlet": ["gauntlet", "run the gauntlet"],
    "subjuction": ["subjection"],
    "marginal increase": ["marginal"],
    "of little/ no avail": ["avail"],
    "to no avail": ["avail"],
    "hold the line": ["line"],
    "run the gamut": ["gamut"],
    "subject a to b": ["subject"],
    "under oath": ["oath"],
}

# Manual (example, phonetic) when API cannot resolve — GRE-style sentences
GRE_MANUAL: dict[str, tuple[str, str]] = {
    "abreast of": (
        "Analysts must stay abreast of regulatory changes that affect the sector.",
        "/əˈbrest ɒv/",
    ),
    "abrogated": (
        "The treaty was abrogated after both parties failed to meet their obligations.",
        "/ˈabrəɡeɪtɪd/",
    ),
    "acquitted": (
        "The defendant was acquitted on all charges after the jury deliberated for two days.",
        "/əˈkwɪtɪd/",
    ),
    "adulterate": (
        "The report argues that profit motives may adulterate scientific findings.",
        "/əˈdʌltəreɪt/",
    ),
    "arresting": (
        "The opening presents an arresting image that frames the author's thesis.",
        "/əˈrestɪŋ/",
    ),
    "destitude": (
        "The memoir describes years of destitute living after the factory closed.",
        "/ˈdɛstɪtjuːt/",
    ),
    "desultority": (
        "Critics noted the desultory structure of the essay, which jumped between unrelated claims.",
        "/ˈdɛsəltəri/",
    ),
    "discern": (
        "Readers must discern which evidence supports the conclusion and which is merely anecdotal.",
        "/dɪˈsɜːrn/",
    ),
    "exacting": (
        "The professor set exacting standards for clarity and logical rigor.",
        "/ɪɡˈzæktɪŋ/",
    ),
    "foibles": (
        "The biography acknowledges the leader's foibles without excusing them.",
        "/ˈfɔɪbəlz/",
    ),
    "habour": (
        "The port continues to harbour vessels despite the sanctions.",
        "/ˈhɑːrbər/",
    ),
    "halcygon": (
        "They recalled a halcyon period before the conflict reshaped the region.",
        "/ˈhælsiən/",
    ),
    "hold the line": (
        "The central bank vowed to hold the line on inflation targets.",
        "/hoʊld ðə laɪn/",
    ),
    "holi-polloi": (
        "The policy was designed for elites, not for the hoi polloi.",
        "/ˌhɔɪ pəˈlɔɪ/",
    ),
    "insensibly": (
        "Costs rose insensibly until the program became unsustainable.",
        "/ɪnˈsɛnsɪbli/",
    ),
    "interred": (
        "The remains were interred in a ceremony attended by diplomats.",
        "/ɪnˈtɜːrd/",
    ),
    "listlessly": (
        "He answered listlessly, as though the question barely registered.",
        "/ˈlɪstləsli/",
    ),
    "marginal increase": (
        "The data show only a marginal increase in output over the quarter.",
        "/ˈmɑːrdʒɪnl ˈɪŋkriːs/",
    ),
    "marginalize": (
        "The essay claims that the reform will marginalize rural communities.",
        "/ˈmɑːrdʒɪnəlaɪz/",
    ),
    "of little/ no avail": (
        "Their protests were of little avail against the entrenched bureaucracy.",
        "/əˈveɪl/",
    ),
    "poultices": (
        "Medieval texts describe poultices applied to wounds after battle.",
        "/ˈpoʊltɪsɪz/",
    ),
    "preordain": (
        "The narrator suggests that outcomes were not preordained but contingent.",
        "/ˌpriːɔːrˈdeɪn/",
    ),
    "preposterous": (
        "The reviewer dismissed the theory as preposterous and unsupported.",
        "/prɪˈpɒstərəs/",
    ),
    "quitoxic": (
        "His quixotic campaign won admiration but few votes.",
        "/kwɪkˈsɒtɪk/",
    ),
    "revoke": (
        "The agency may revoke the license if safety standards are violated.",
        "/rɪˈvoʊk/",
    ),
    "roundly": (
        "The proposal was roundly criticized in the editorial pages.",
        "/ˈraʊndli/",
    ),
    "run the gamut": (
        "The study runs the gamut from fiscal policy to cultural history.",
        "/rʌn ðə ˈɡæmɪt/",
    ),
    "run the gaunlet": (
        "Whistleblowers often run the gauntlet of public scrutiny and legal threat.",
        "/rʌn ðə ˈɡɔːntlt/",
    ),
    "slanderous": (
        "The editorial contained slanderous claims that were later retracted.",
        "/ˈslændərəs/",
    ),
    "solipsistic": (
        "The argument struck readers as solipsistic, grounded only in personal anecdote.",
        "/ˌsɒlɪpˈsɪstɪk/",
    ),
    "stalemate": (
        "Negotiations reached a stalemate after neither side offered concessions.",
        "/ˈsteɪlmeɪt/",
    ),
    "subject a to b": (
        "The clause subjects applicants to a battery of background checks.",
        "/səbˈdʒɛkt/",
    ),
    "subjuction": (
        "The passage discusses the subjection of minorities under colonial rule.",
        "/səbˈdʒɛkʃn/",
    ),
    "surrogate": (
        "Surrogate measures of wealth can mislead policy makers.",
        "/ˈsʌrəɡət/",
    ),
    "terminus": (
        "The railway terminus became a symbol of industrial modernity.",
        "/ˈtɜːrmɪnəs/",
    ),
    "to no avail": (
        "He appealed the ruling, but to no avail.",
        "/əˈveɪl/",
    ),
    "under oath": (
        "Witnesses testified under oath about the sequence of events.",
        "/ˈʌndər oʊθ/",
    ),
    "vicissitudes": (
        "The memoir traces the vicissitudes of exile and return.",
        "/vɪˈsɪsɪtjuːdz/",
    ),
    "vitiating": (
        "A single logical flaw may be vitiating the entire argument.",
        "/ˈvɪʃieɪtɪŋ/",
    ),
    "warily": (
        "Investors responded warily to the central bank's announcement.",
        "/ˈweərɪli/",
    ),
}


async def _resolve_from_api(
    display_word: str, pos_hint: str
) -> tuple[str, str, Optional[str], Optional[str]]:
    """Try display word + aliases; return (example, phonetic, audio)."""
    keys = [display_word] + GRE_API_ALIASES.get(display_word, [])
    for key in keys:
        entry = await fetch_entry(key)
        if not entry:
            continue
        _, example, _ = pick_definition(entry, pos_hint)
        phonetic, audio = pick_phonetic_audio(entry)
        return example, phonetic or "", audio
    return "", "", None


async def fill_gre_gaps(repo: VocabLexiconRepo, *, dry_run: bool = False) -> dict:
    stmt = (
        select(VocabPackItem)
        .where(VocabPackItem.pack_id == "pack_gre")
        .options(selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.senses))
        .order_by(VocabPackItem.order_index)
    )
    items = [i for i in (await repo.s.execute(stmt)).scalars().all() if i.lexeme]
    gaps = [
        i
        for i in items
        if _needs_example_ipa_backfill(_primary_sense_from_item(i), "gre")
    ]
    stats = {"gaps": len(gaps), "ok": 0, "dry": 0}

    logger.info("GRE gap fill: {} words need example and/or IPA", len(gaps))

    for item in gaps:
        lexeme = item.lexeme
        sense = _primary_sense_from_item(item)
        if not sense:
            continue
        word = lexeme.display_word
        need_ex = not _has_usable_example(sense, "gre")
        need_ipa = not _has_usable_phonetic(sense)

        api_ex, api_ph, audio = await _resolve_from_api(word, lexeme.pos or "")
        manual = GRE_MANUAL.get(word) or GRE_MANUAL.get(word.lower())

        if manual:
            man_ex, man_ph = manual
            if need_ex and man_ex:
                api_ex = man_ex
            if need_ipa and man_ph:
                api_ph = man_ph

        if need_ex and not api_ex.strip():
            api_ex = f"The passage uses {word} to sharpen the author's claim."

        phonetic = sense.phonetic
        audio_url = sense.audio_url
        if need_ipa and api_ph:
            phonetic = api_ph
        elif need_ipa and manual:
            phonetic = manual[1]

        ielts_ex, gre_ex = _example_for_pack(
            lexeme, "gre", api_example=api_ex, existing=sense
        )

        if dry_run:
            stats["dry"] += 1
            logger.info("DRY {} | ex={} | ipa={}", word, bool(gre_ex), bool(phonetic))
            continue

        await repo.upsert_primary_sense(
            lexeme.id,
            definition_en=sense.definition_en,
            vi_gloss=sense.vi_gloss,
            vi_translate_prompt=sense.vi_translate_prompt,
            topic_prompt=sense.topic_prompt,
            usage_note=sense.usage_note,
            ielts_example=ielts_ex,
            gre_example=gre_ex,
            phonetic=phonetic,
            audio_url=audio_url,
            topic_tags=list(sense.topic_tags) if sense.topic_tags else [lexeme.lemma],
            tips=list(sense.tips) if isinstance(sense.tips, list) else [],
        )
        stats["ok"] += 1
        await asyncio.sleep(0.15)

    if not dry_run:
        await repo.s.commit()
    logger.info("GRE gap fill done | patched={} dry_run={}", stats["ok"], dry_run)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fill GRE example/IPA gaps in DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)
        await fill_gre_gaps(repo, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
