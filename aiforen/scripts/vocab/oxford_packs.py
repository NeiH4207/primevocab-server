"""Oxford 5000 / CEFR packs (A1–C2) — separate from IELTS band packs."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

OXFORD_SOURCE = "oxford_5000_thanhtuan"
OXFORD_CSV_NAME = "oxford_5000_thanhtuan.csv"

LEVEL_TO_PACK: Dict[str, str] = {
    "A1": "pack_oxford_a1",
    "A2": "pack_oxford_a2",
    "B1": "pack_oxford_b1",
    "B2": "pack_oxford_b2",
    "C1": "pack_oxford_c1",
    "C2": "pack_oxford_c2",
}

# Approximate IELTS band range for UI filters (not exam targets).
CEFR_IELTS_BAND: Dict[str, Tuple[float, float]] = {
    "A1": (3.0, 4.5),
    "A2": (4.0, 5.5),
    "B1": (5.0, 6.5),
    "B2": (6.0, 7.5),
    "C1": (7.0, 9.0),
    "C2": (8.0, 9.0),
}

POS_MAP: Dict[str, str] = {
    "n": "noun",
    "noun": "noun",
    "v": "verb",
    "verb": "verb",
    "adj": "adj",
    "adv": "adv",
    "prep": "adv",
    "conj": "conj",
    "det": "noun",
    "pron": "noun",
    "article": "noun",
    "modal": "verb",
    "number": "noun",
    "interj": "adv",
    "exclam": "adv",
}


def canonical_lemma(raw: str) -> str:
    """Primary lemma for lexeme id (first alternative before comma)."""
    text = (raw or "").strip().lower()
    return text.split(",")[0].strip() or text


def normalize_pos(raw: str) -> str:
    if not raw:
        return "noun"
    s = str(raw).strip().lower().rstrip(".")
    first = re.split(r"[,/\s]+", s)[0].strip().rstrip(".")
    if first in POS_MAP:
        return POS_MAP[first]
    if first.startswith("n"):
        return "noun"
    if first.startswith("v"):
        return "verb"
    if "adj" in first:
        return "adj"
    if "adv" in first:
        return "adv"
    if "conj" in first:
        return "conj"
    if "prep" in first:
        return "adv"
    return "noun"


OXFORD_PACKS: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_oxford_a1",
        "title": "Oxford 5000 — A1",
        "description": "Most common English words at CEFR A1 (Oxford 5000 — Thanh Tuấn).",
        "category": "Oxford A1",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "A1",
        "oxford_level": "A1",
        "sort_order": 11,
        "is_premium": False,
    },
    {
        "pack_id": "pack_oxford_a2",
        "title": "Oxford 5000 — A2",
        "description": "CEFR A2 core vocabulary from the Oxford 5000 word list.",
        "category": "Oxford A2",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "A2",
        "oxford_level": "A2",
        "sort_order": 12,
        "is_premium": False,
    },
    {
        "pack_id": "pack_oxford_b1",
        "title": "Oxford 5000 — B1",
        "description": "CEFR B1 vocabulary for intermediate learners.",
        "category": "Oxford B1",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "B1",
        "oxford_level": "B1",
        "sort_order": 13,
        "is_premium": False,
    },
    {
        "pack_id": "pack_oxford_b2",
        "title": "Oxford 5000 — B2",
        "description": "CEFR B2 upper-intermediate Oxford 5000 words.",
        "category": "Oxford B2",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "B2",
        "oxford_level": "B2",
        "sort_order": 14,
        "is_premium": True,
    },
    {
        "pack_id": "pack_oxford_c1",
        "title": "Oxford 5000 — C1",
        "description": "CEFR C1 advanced common words (Oxford 5000).",
        "category": "Oxford C1",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "C1",
        "oxford_level": "C1",
        "sort_order": 15,
        "is_premium": True,
    },
    {
        "pack_id": "pack_oxford_c2",
        "title": "Oxford 5000 — C2",
        "description": "CEFR C2 proficiency vocabulary (extended curated list).",
        "category": "Oxford C2",
        "pack_family": "cefr",
        "exam_type": "oxford",
        "cefr_level": "C2",
        "oxford_level": "C2",
        "sort_order": 16,
        "is_premium": True,
    },
]
