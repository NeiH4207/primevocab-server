"""Studied vs content CEFR for vocab coaching.

Learners pick their attained band (studied level). Daily readings and word mix use content
one step above (+1 CEFR): Newbie→A1, A1→A2, …, C1→C2. C2 is not selectable in the switcher.
"""

from __future__ import annotations

CONTENT_CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
STUDIED_COACHING_LEVELS = ("NEWBIE", "A1", "A2", "B1", "B2", "C1")


def normalize_studied_level(level: str | None) -> str:
    raw = (level or "NEWBIE").strip().upper()
    if raw in ("NEWBIE", "BEGINNER", "A0"):
        return "NEWBIE"
    if raw == "C2":
        return "C1"
    if raw in CONTENT_CEFR_LEVELS and raw != "C2":
        return raw
    return "NEWBIE"


def content_cefr_for_studied(studied: str | None) -> str:
    """Content delivered one CEFR step above the studied (attained) level."""
    key = normalize_studied_level(studied)
    if key == "NEWBIE":
        return "A1"
    idx = CONTENT_CEFR_LEVELS.index(key)
    return CONTENT_CEFR_LEVELS[min(idx + 1, len(CONTENT_CEFR_LEVELS) - 1)]


def studied_cefr_from_content(content: str | None) -> str:
    """Best-effort studied level for legacy plans that only stored content CEFR."""
    cefr = (content or "A1").strip().upper()
    if cefr == "A1":
        return "NEWBIE"
    if cefr not in CONTENT_CEFR_LEVELS:
        return "B1"
    idx = CONTENT_CEFR_LEVELS.index(cefr)
    return CONTENT_CEFR_LEVELS[idx - 1]


def studied_level_from_calibration_result(calibrated_cefr: str | None) -> str:
    """Quick-check estimates attainment — map to switcher studied level (cap at C1)."""
    cefr = (calibrated_cefr or "").strip().upper()
    if cefr in ("A1", "A0", ""):
        return "NEWBIE"
    if cefr == "C2":
        return "C1"
    if cefr in STUDIED_COACHING_LEVELS:
        return cefr
    return "NEWBIE"
