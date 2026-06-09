"""Unit tests for the Quick Vocab Check self-calibration domain logic.

These guard the calibration scoring, level estimation, and the
"suspicious self-rating" detector (which feeds confidence + the
`is_suspicious` flag the frontend surfaces to learners).
"""

from typing import Dict, List

from aiforen.domain.quick_vocab_check import (
    QUICK_VOCAB_CHECK_ITEMS_32,
    build_calibration_review,
    calculate_band_scores,
    detect_suspicious_self_rating,
    estimate_vocab_level,
    get_check_preset,
    get_quick_vocab_check_result,
    normalize_answer_row,
    normalize_check_size,
)

# rating int encoding used by the API (LEVEL_INT_TO_RATING): New/Seen/Know/Use
_RATING_INT = {"New": 0, "Seen": 1, "Know": 2, "Use": 3}


def _answers_by_cefr(rating_by_cefr: Dict[str, str]) -> List[Dict[str, object]]:
    """Build raw API-shaped answers for the 32-word check from a CEFR->rating map."""
    return [
        {
            "word": item["word"],
            "level": _RATING_INT[rating_by_cefr[item["level"]]],
        }
        for item in QUICK_VOCAB_CHECK_ITEMS_32
    ]


def _normalize(raw: List[Dict[str, object]]):
    preset = get_check_preset(32)
    lookup = {item["word"].lower(): item["level"] for item in preset.items}
    rows = [normalize_answer_row(a, word_lookup=lookup) for a in raw]
    return [r for r in rows if r], preset


def test_normalize_check_size_clamps_to_allowed_presets():
    assert normalize_check_size(32) == 32
    assert normalize_check_size(48) == 48
    assert normalize_check_size(60) == 60
    # Unknown / junk sizes fall back to the smallest preset.
    assert normalize_check_size(40) == 32
    assert normalize_check_size("nonsense") == 32
    assert normalize_check_size(None) == 32


def test_band_scores_cover_every_cefr_level_once():
    raw = _answers_by_cefr(
        {"A2": "Use", "B1": "Use", "B2": "Know", "C1": "Seen", "C2": "New"}
    )
    rows, preset = _normalize(raw)
    bands = calculate_band_scores(rows, preset)
    assert [b["level"] for b in bands] == ["A2", "B1", "B2", "C1", "C2"]
    # Every check item is accounted for in exactly one band.
    assert sum(b["total"] for b in bands) == len(QUICK_VOCAB_CHECK_ITEMS_32)
    # Scores are bounded percentages.
    assert all(0 <= b["score"] <= 100 for b in bands)


def test_estimate_level_picks_highest_solid_band():
    raw = _answers_by_cefr(
        {"A2": "Use", "B1": "Use", "B2": "Use", "C1": "Seen", "C2": "New"}
    )
    rows, preset = _normalize(raw)
    result = estimate_vocab_level(rows, preset)
    assert result["estimatedLevel"].startswith("B2")


def test_strong_consistent_learner_is_not_flagged_suspicious():
    raw = _answers_by_cefr(
        {"A2": "Use", "B1": "Use", "B2": "Know", "C1": "Seen", "C2": "New"}
    )
    rows, preset = _normalize(raw)
    flags = detect_suspicious_self_rating(rows, preset)
    assert flags["isSuspicious"] is False

    review = build_calibration_review(raw, locale="en", check_size=32)
    assert review["cefr_level"] == "B2"
    assert review["is_suspicious"] is False
    assert 0.0 <= review["confidence"] <= 1.0


def test_inflated_c_level_self_rating_is_flagged_suspicious():
    """Marking every C1/C2 word as 'Use' while every A2/B1 word is 'New'
    is internally inconsistent and must be detected (regression guard for the
    C-level use-rate branch)."""
    raw = _answers_by_cefr(
        {"A2": "New", "B1": "New", "B2": "New", "C1": "Use", "C2": "Use"}
    )
    rows, preset = _normalize(raw)
    flags = detect_suspicious_self_rating(rows, preset)
    assert flags["isSuspicious"] is True
    assert flags["flags"], "expected at least one explanatory flag"

    result = get_quick_vocab_check_result(raw, check_size=32)
    assert result["isSuspicious"] is True


def test_empty_answers_are_handled_gracefully():
    flags = detect_suspicious_self_rating([], get_check_preset(32))
    assert flags == {"suspicion": 0, "isSuspicious": False, "flags": []}

    review = build_calibration_review([], locale="vi", check_size=32)
    # Below-A2 estimate with no data must still return a well-formed payload.
    assert review["check_size"] == 32
    assert "summary" in review and review["summary"]
    assert isinstance(review["bands"], list)
