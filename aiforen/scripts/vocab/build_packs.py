"""Unified IELTS band + GRE packs (one pack per band level).

Topic/skill labels are stored on pack items for analytics only — not separate packs.
Writing-specific packs (Task 1, Task 2) will use pack_family='writing' later.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from loguru import logger

from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo, lexeme_id_for

WordTuple = Tuple[str, str]

# Merged core words per band (from legacy seed + former thematic cores).
BAND_PACKS: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_band_4",
        "title": "Band 4 Vocabulary",
        "description": "High-frequency IELTS words for band 4 — all skills (listening, reading, speaking, writing).",
        "category": "Band 4",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 4.0,
        "target_band_max": 5.0,
        "sort_order": 1,
        "words": [
            ("important", "adj"),
            ("people", "noun"),
            ("job", "noun"),
            ("study", "verb"),
            ("better", "adj"),
            ("healthy", "adj"),
            ("increase", "verb"),
            ("decrease", "verb"),
            ("change", "noun"),
            ("problem", "noun"),
            ("reason", "noun"),
        ],
    },
    {
        "pack_id": "pack_band_5",
        "title": "Band 5 Vocabulary",
        "description": "Core IELTS lexis for band 5 learners across everyday and academic topics.",
        "category": "Band 5",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 5.0,
        "target_band_max": 6.0,
        "sort_order": 2,
        "words": [
            ("benefit", "noun"),
            ("effect", "noun"),
            ("improve", "verb"),
            ("support", "verb"),
            ("choice", "noun"),
            ("compare", "verb"),
            ("reduce", "verb"),
            ("create", "verb"),
            ("common", "adj"),
            ("healthy", "adj"),
        ],
    },
    {
        "pack_id": "pack_band_6",
        "title": "Band 6 Vocabulary",
        "description": "Useful academic-general words for IELTS band 6 (not limited to one writing task).",
        "category": "Band 6",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 6.0,
        "target_band_max": 7.0,
        "sort_order": 3,
        "is_premium": False,
        "words": [
            ("proportion", "noun"),
            ("overall", "adv"),
            ("stable", "adj"),
            ("marginal", "adj"),
            ("whereas", "conj"),
            ("substantial", "adj"),
            ("evidence", "noun"),
            ("drawback", "noun"),
            ("outcome", "noun"),
            ("beneficial", "adj"),
        ],
    },
    {
        "pack_id": "pack_band_7",
        "title": "Band 7 Vocabulary",
        "description": "Academic words for precise meaning and natural usage at band 7.",
        "category": "Band 7",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 7.0,
        "target_band_max": 8.0,
        "sort_order": 4,
        "is_premium": True,
        "words": [
            ("mitigate", "verb"),
            ("sustainable", "adj"),
            ("fluctuate", "verb"),
            ("contribute", "verb"),
            ("emphasise", "verb"),
            ("inequality", "noun"),
            ("arguably", "adv"),
            ("scrutinise", "verb"),
            ("aspiration", "noun"),
            ("redundant", "adj"),
        ],
    },
    {
        "pack_id": "pack_band_8",
        "title": "Band 8 Vocabulary",
        "description": "Higher-band vocabulary for nuance and evaluation in IELTS contexts.",
        "category": "Band 8",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 8.0,
        "target_band_max": 9.0,
        "sort_order": 5,
        "is_premium": True,
        "words": [
            ("nuanced", "adj"),
            ("plausible", "adj"),
            ("robust", "adj"),
            ("compelling", "adj"),
            ("trade-off", "noun"),
            ("undermine", "verb"),
            ("prevalent", "adj"),
            ("constraint", "noun"),
            ("counterproductive", "adj"),
            ("disproportionate", "adj"),
        ],
    },
    {
        "pack_id": "pack_band_9",
        "title": "Band 9 Vocabulary",
        "description": "Precise, flexible lexis for top-band IELTS performance.",
        "category": "Band 9",
        "pack_family": "band",
        "exam_type": "ielts",
        "target_band_min": 8.5,
        "target_band_max": 9.0,
        "sort_order": 6,
        "is_premium": True,
        "words": [
            ("salient", "adj"),
            ("pragmatic", "adj"),
            ("detrimental", "adj"),
            ("exacerbate", "verb"),
            ("ubiquitous", "adj"),
            ("intricate", "adj"),
            ("paradigm", "noun"),
            ("contentious", "adj"),
            ("pervasive", "adj"),
            ("inadvertently", "adv"),
        ],
    },
    {
        "pack_id": "pack_gre",
        "title": "GRE Vocabulary",
        "description": "Advanced academic words for GRE Verbal and high-level readers.",
        "category": "GRE",
        "pack_family": "gre",
        "exam_type": "gre",
        "target_band_min": 0.0,
        "target_band_max": 9.0,
        "sort_order": 7,
        "is_premium": True,
        "words": [
            ("equivocal", "adj"),
            ("laconic", "adj"),
            ("prosaic", "adj"),
            ("tenuous", "adj"),
            ("magnanimous", "adj"),
            ("obdurate", "adj"),
            ("recalcitrant", "adj"),
            ("sagacious", "adj"),
            ("aberration", "noun"),
            ("venerate", "verb"),
        ],
    },
]

# Deprecated split packs — kept for migration/deactivation only.
DEPRECATED_PACK_IDS: List[str] = [
    "pack_band4_daily",
    "pack_band4_changes",
    "pack_band5_benefits",
    "pack_band5_compare",
    "pack_band6_task1_trends",
    "pack_band6_task2_argument",
    "pack_band7_environment",
    "pack_band7_argument",
    "pack_band8_evaluation",
    "pack_band8_society",
    "pack_band9_precision",
    "pack_band9_abstract",
    "pack_gre_attitude",
    "pack_gre_character",
    "pack_gre_reasoning",
]

# Alias for scripts that still import THEMATIC_PACKS
THEMATIC_PACKS = BAND_PACKS


async def deactivate_deprecated_packs(repo: VocabLexiconRepo) -> None:
    from sqlalchemy import update

    from aiforen.domain.sql_models import VocabPack

    if not DEPRECATED_PACK_IDS:
        return
    await repo.s.execute(
        update(VocabPack)
        .where(VocabPack.pack_id.in_(DEPRECATED_PACK_IDS))
        .values(is_active=False, content_status="deprecated")
    )
    logger.info("Deactivated {} legacy split packs", len(DEPRECATED_PACK_IDS))


async def build_band_packs(repo: VocabLexiconRepo, *, reset_items: bool = True) -> int:
    """Upsert pack metadata. Set reset_items=False to avoid wiping filled pack_items."""
    await deactivate_deprecated_packs(repo)
    built = 0
    for spec in BAND_PACKS:
        existing_n = len(await repo.list_pack_lexeme_ids(spec["pack_id"]))
        await repo.upsert_pack(
            {
                "pack_id": spec["pack_id"],
                "title": spec["title"],
                "description": spec["description"],
                "category": spec["category"],
                "task_type": "Both",
                "exam_type": spec["exam_type"],
                "pack_family": spec.get("pack_family", "band"),
                "skill_focus": None,
                "topic": None,
                "source_band_min": 0.0,
                "source_band_max": 9.0,
                "target_band_min": spec.get("target_band_min", 0.0),
                "target_band_max": spec.get("target_band_max", 9.0),
                "sort_order": spec["sort_order"],
                "is_active": True,
                "is_premium": spec.get("is_premium", False),
                "content_status": "draft",
                "target_word_count": len(spec["words"]),
                "completed_word_count": 0,
            }
        )
        if reset_items or existing_n == 0:
            lexeme_ids = [lexeme_id_for(lemma, pos) for lemma, pos in spec["words"]]
            await repo.set_pack_items(spec["pack_id"], lexeme_ids)
            logger.info(
                "Built pack {} ({} core words)", spec["pack_id"], len(lexeme_ids)
            )
        else:
            logger.info(
                "Pack {} metadata only — kept {} existing items (reset_items=False)",
                spec["pack_id"],
                existing_n,
            )
        built += 1
    return built


# Back-compat name used by seed / run_pipeline
build_thematic_packs = build_band_packs
