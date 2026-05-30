"""Bootstrap PG vocab lexicon from legacy seed word rows + questions."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from loguru import logger

from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.seed import PACK_WORD_ROWS, VOCAB_PACKS, _derive_translate_prompt

_PACK_TOPICS: Dict[str, str] = {
    "Band 4": "công nghệ và cuộc sống hàng ngày",
    "Band 5": "môi trường và sức khoẻ cộng đồng",
    "Band 6": "giáo dục, việc làm và đô thị",
    "Band 7": "biến đổi khí hậu và chính sách công",
    "Band 8": "xã hội, công việc và truyền thông",
    "Band 9": "công nghệ, văn hoá và toàn cầu hoá",
    "GRE": "nghiên cứu, phân tích và lập luận học thuật",
}


def _exam_type_for_category(category: str) -> List[str]:
    if category == "GRE":
        return ["gre"]
    return ["ielts"]


def _gre_tier(band: float) -> Optional[str]:
    if band < 8:
        return "medium"
    return "hard"


async def bootstrap_lexemes_from_legacy(
    repo: VocabLexiconRepo,
    *,
    approve: bool = True,
) -> Dict[str, Any]:
    """Import all PACK_WORD_ROWS into vocab_lexemes + senses + legacy map."""

    stats = {"lexemes": 0, "legacy_maps": 0}
    packs_by_id = {p["pack_id"]: p for p in VOCAB_PACKS}

    for pack_id, pack_rows in PACK_WORD_ROWS.items():
        pack = packs_by_id[pack_id]
        category = pack["category"]
        exam_types = _exam_type_for_category(category)
        topic = _PACK_TOPICS.get(category, "một vấn đề học thuật")

        for idx, (word, pos, definition, band, usage, vi_prompt) in enumerate(
            pack_rows, start=1
        ):
            legacy_id = f"{pack_id}_{idx:02d}"
            translate_prompt = _derive_translate_prompt(vi_prompt)
            topic_prompt = f"Viết một câu tiếng Anh dùng '{word}' về chủ đề {topic}."
            example = f"Learners can use '{word}' accurately when the context is clear."

            lexeme = await repo.upsert_lexeme(
                lemma=word,
                pos=pos,
                display_word=word,
                ielts_band_min=band,
                ielts_band_max=band,
                gre_tier=_gre_tier(band) if "gre" in exam_types else None,
                is_academic=band >= 7.0,
                exam_types=exam_types,
                sources=[{"name": "aiforen_seed", "license": "proprietary"}],
                status="approved" if approve else "enriched",
            )
            await repo.upsert_primary_sense(
                lexeme.id,
                definition_en=definition,
                vi_translate_prompt=translate_prompt,
                topic_prompt=topic_prompt,
                usage_note=usage,
                ielts_example=example,
                topic_tags=[category.lower().replace(" ", "_"), pack_id],
                tips=[
                    "Use it only when the meaning is precise.",
                    "Put it in a complete sentence with a clear IELTS context.",
                ],
            )
            await repo.upsert_legacy_map(legacy_id, lexeme.id, pack_id=pack_id)
            stats["lexemes"] += 1
            stats["legacy_maps"] += 1

    logger.info("Bootstrap lexemes: {}", stats)
    return stats


def _default_mcq(word: str, example: str) -> Tuple[str, List[Dict[str, str]], str, str]:
    options = [
        {"id": "a", "text": example},
        {"id": "b", "text": f"The {word} is very people and many thing."},
        {"id": "c", "text": f"I {word} go to school yesterday."},
        {"id": "d", "text": f"It is {word} because it is."},
    ]
    prompt = f"Which sentence uses '{word}' most naturally?"
    return (
        prompt,
        options,
        "a",
        "Option A uses the word in a grammatically complete sentence.",
    )


async def bootstrap_questions_for_all(repo: VocabLexiconRepo) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from aiforen.domain.sql_models import VocabLexeme

    stmt = (
        select(VocabLexeme)
        .where(VocabLexeme.status != "deprecated")
        .options(selectinload(VocabLexeme.senses))
    )
    lexemes = (await repo.s.execute(stmt)).scalars().all()
    count = 0
    for lx in lexemes:
        sense = repo._primary_sense(lx)
        if not sense:
            continue
        example = sense.ielts_example or f"This shows how to use {lx.display_word}."
        prompt, options, correct, expl = _default_mcq(lx.display_word, example)
        await repo.upsert_question(
            lx.id,
            qtype="meaning_mcq",
            prompt=prompt,
            options=options,
            correct_option_id=correct,
            explanation=expl,
            status="approved",
            sense_id=sense.id,
            generator_meta={"source": "bootstrap"},
        )
        count += 1
    logger.info("Bootstrap questions: {}", count)
    return count
