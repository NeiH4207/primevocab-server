"""CEFR vocabulary level estimation for quick calibration (not IELTS band scoring)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

CEFR_LEVELS: Tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")
_LEVEL_INDEX = {level: index for index, level in enumerate(CEFR_LEVELS)}

# Approximate IELTS band range for optional UI hint only.
CEFR_IELTS_HINT: Dict[str, Tuple[float, float]] = {
    "A1": (3.0, 4.0),
    "A2": (4.0, 5.0),
    "B1": (5.0, 6.0),
    "B2": (6.0, 7.0),
    "C1": (7.0, 8.0),
    "C2": (8.0, 9.0),
}

_LABEL_TO_CEFR: Dict[str, str] = {
    "A1": "A1",
    "A2": "A2",
    "B1": "B1",
    "B2": "B2",
    "C1": "C1",
    "C2": "C2",
    "FOUNDATION": "A1",
    "BASIC": "A2",
    "INTERMEDIATE": "B1",
    "UPPER-INTERMEDIATE": "B2",
    "UPPER INTERMEDIATE": "B2",
    "ADVANCED": "C1",
    "EXTREME": "C2",
    "BAND 4": "A2",
    "BAND 4.5": "A2",
    "BAND 5": "B1",
    "BAND 5.5": "B1",
    "BAND 6": "B1",
    "BAND 6.5": "B2",
    "BAND 7": "B2",
    "BAND 7.5": "C1",
    "BAND 8": "C1",
    "BAND 8.5": "C2",
    "BAND 9": "C2",
}

_LEVEL_NAMES_VI = {
    "A1": "sơ cấp (A1)",
    "A2": "cơ bản (A2)",
    "B1": "trung cấp (B1)",
    "B2": "trung cấp khá (B2)",
    "C1": "cao cấp (C1)",
    "C2": "thành thạo (C2)",
}


def normalize_cefr_label(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip().upper()
    if text in _LEVEL_INDEX:
        return text
    if text in _LABEL_TO_CEFR:
        return _LABEL_TO_CEFR[text]
    match = re.search(r"BAND\s*([4-9](?:\.5)?)", text)
    if match:
        return _LABEL_TO_CEFR.get(f"BAND {match.group(1)}")
    return None


def _answer_level(answer: Dict[str, Any]) -> int:
    try:
        return max(0, min(3, int(answer.get("level") or 0)))
    except Exception:
        return 0


def estimate_cefr_from_answers(answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rule-based CEFR estimate from self-reported word familiarity."""
    if not answers:
        return {
            "cefr_level": "A2",
            "confidence": 0.2,
            "known_ratio": 0.0,
            "production_ratio": 0.0,
        }

    weighted_sum = 0.0
    weight_total = 0.0
    known = 0
    production = 0

    for answer in answers:
        cefr = normalize_cefr_label(
            answer.get("calibration_label")
        ) or normalize_cefr_label(answer.get("cefr_label"))
        if not cefr:
            try:
                band = float(answer.get("band") or 6.0)
                if band < 5.0:
                    cefr = "A2"
                elif band < 6.0:
                    cefr = "B1"
                elif band < 7.0:
                    cefr = "B2"
                elif band < 8.0:
                    cefr = "C1"
                else:
                    cefr = "C2"
            except Exception:
                cefr = "B1"

        level = _answer_level(answer)
        if level >= 2:
            known += 1
        if level >= 3:
            production += 1

        # Level weight: New=0, Seen=0.35, Know=0.72, Use=1.0
        level_weight = {0: 0.0, 1: 0.35, 2: 0.72, 3: 1.0}.get(level, 0.0)
        tier_index = _LEVEL_INDEX.get(cefr, 2)
        weighted_sum += tier_index * level_weight
        weight_total += level_weight if level_weight > 0 else 0.15

    if weight_total <= 0:
        score = 1.0
    else:
        score = weighted_sum / weight_total

    # Slight boost when many "Use" at harder tiers
    hard_use = sum(
        1
        for a in answers
        if _answer_level(a) >= 3
        and _LEVEL_INDEX.get(
            normalize_cefr_label(a.get("calibration_label")) or "B1", 0
        )
        >= 3
    )
    if hard_use >= 2:
        score = min(len(CEFR_LEVELS) - 1, score + 0.35)

    index = int(round(max(0, min(len(CEFR_LEVELS) - 1, score))))
    cefr_level = CEFR_LEVELS[index]

    n = len(answers)
    known_ratio = known / n
    production_ratio = production / n
    spread = abs(known_ratio - 0.5) + production_ratio * 0.25
    confidence = min(0.88, 0.32 + n / 36 + spread * 0.35)

    return {
        "cefr_level": cefr_level,
        "confidence": round(confidence, 2),
        "known_ratio": round(known_ratio, 2),
        "production_ratio": round(production_ratio, 2),
    }


def cefr_to_ielts_mid(cefr_level: str) -> float:
    low, high = CEFR_IELTS_HINT.get(cefr_level, (5.5, 6.5))
    return round((low + high) / 2, 1)


def cefr_display_name(cefr_level: str, *, locale: str = "vi") -> str:
    if str(locale).lower().startswith("vi"):
        return _LEVEL_NAMES_VI.get(cefr_level, cefr_level)
    return cefr_level


def pick_recommended_pack_id(
    cefr_level: str,
    packs: List[Dict[str, Any]],
) -> Optional[str]:
    if not packs:
        return None

    oxford_id = {
        "A1": "pack_oxford_a1",
        "A2": "pack_oxford_a2",
        "B1": "pack_oxford_b1",
        "B2": "pack_oxford_b2",
        "C1": "pack_oxford_c1",
    }.get(cefr_level)
    if oxford_id and any(str(p.get("pack_id")) == oxford_id for p in packs):
        return oxford_id

    target_band = cefr_to_ielts_mid(cefr_level)
    band_packs = [
        p
        for p in packs
        if (p.get("pack_family") or "band") == "band"
        and float(p.get("source_band_min") or 0)
        <= target_band
        <= float(p.get("target_band_max") or 9)
    ]
    if band_packs:
        return str(band_packs[0].get("pack_id"))

    return str(packs[0].get("pack_id")) if packs else None
