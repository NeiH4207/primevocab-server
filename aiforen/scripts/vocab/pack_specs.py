"""One pack per IELTS band / GRE — selection rules + stat label hints (analytics only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from aiforen.scripts.vocab.build_packs import BAND_PACKS

WordTuple = Tuple[str, str]

# Product targets (fill packs to min(goal, pool size); enrich/transipy later).
PACK_TARGET_GOALS: Dict[str, int] = {
    "pack_band_4": 700,
    "pack_band_5": 900,
    "pack_band_6": 1150,
    "pack_band_7": 1400,
    "pack_band_8": 1200,
    "pack_band_9": 1000,
    "pack_gre": 969,
}

# Topic labels attached to words for stats/dashboards — not separate packs.
STAT_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "education": [
        "education",
        "school",
        "student",
        "learn",
        "study",
        "university",
        "teach",
    ],
    "health": ["health", "medical", "disease", "doctor", "patient", "fitness"],
    "environment": [
        "environment",
        "climate",
        "pollution",
        "energy",
        "nature",
        "carbon",
    ],
    "technology": [
        "technology",
        "digital",
        "internet",
        "computer",
        "innovation",
        "device",
    ],
    "society": ["society", "social", "community", "culture", "people", "public"],
    "work": ["work", "job", "employ", "career", "business", "economic", "market"],
    "government": ["government", "policy", "law", "legal", "politic", "regulation"],
}


def infer_stat_labels(lemma: str) -> List[str]:
    labels: List[str] = []
    low = lemma.lower()
    for label, keys in STAT_TOPIC_KEYWORDS.items():
        if any(k in low for k in keys):
            labels.append(label)
    return labels or ["general"]


def _spec(
    pack: Dict[str, Any],
    *,
    rank_min: int,
    rank_max: int,
    prefer_academic: bool = False,
    target_count: Optional[int] = None,
    core_only: bool = False,
    pool_mode: str = "rank",
) -> Dict[str, Any]:
    pack_id = pack["pack_id"]
    goal = (
        target_count if target_count is not None else PACK_TARGET_GOALS.get(pack_id, 20)
    )
    return {
        **pack,
        "target_count": goal,
        "rank_min": rank_min,
        "rank_max": rank_max,
        "prefer_academic": prefer_academic,
        "core_words": list(pack.get("words") or []),
        "core_only": core_only,
        "pool_mode": pool_mode,
    }


PACK_SPECS: List[Dict[str, Any]] = [
    _spec(BAND_PACKS[0], rank_min=1, rank_max=900),
    _spec(BAND_PACKS[1], rank_min=150, rank_max=1100),
    _spec(BAND_PACKS[2], rank_min=600, rank_max=1700),
    _spec(BAND_PACKS[3], rank_min=900, rank_max=2200, prefer_academic=True),
    _spec(BAND_PACKS[4], rank_min=1300, rank_max=2399, prefer_academic=True),
    # Top NGSL (rank ≥2000) + NAWL academic (null rank) to reach ~1000.
    _spec(BAND_PACKS[5], rank_min=2000, rank_max=2809, prefer_academic=True),
    # Curated GRE list from Vocabulary.xlsx (imported with exam_types gre).
    _spec(BAND_PACKS[6], rank_min=1, rank_max=2809, pool_mode="xlsx"),
]


def get_pack_spec(pack_id: str) -> Optional[Dict[str, Any]]:
    for spec in PACK_SPECS:
        if spec["pack_id"] == pack_id:
            return spec
    from aiforen.scripts.vocab.oxford_packs import OXFORD_PACKS

    for spec in OXFORD_PACKS:
        if spec["pack_id"] == pack_id:
            return spec
    return None
