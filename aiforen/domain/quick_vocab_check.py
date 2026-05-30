"""Quick Vocab Check — 32 / 48 / 60-word self-calibration (A2–C2, no A1)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

CEFRLevel = Literal["A2", "B1", "B2", "C1", "C2"]
VocabRating = Literal["New", "Seen", "Know", "Use"]
BandStatus = Literal["solid", "developing", "weak", "not-ready"]
CheckSize = Literal[32, 48, 60]

CEFR_LEVELS: tuple[str, ...] = ("A2", "B1", "B2", "C1", "C2")
ALLOWED_CHECK_SIZES: tuple[int, ...] = (32, 48, 60)

RATING_SCORE: Dict[str, float] = {
    "New": 0.0,
    "Seen": 0.33,
    "Know": 0.75,
    "Use": 1.0,
}

LEVEL_INT_TO_RATING: Dict[int, VocabRating] = {
    0: "New",
    1: "Seen",
    2: "Know",
    3: "Use",
}

QUICK_VOCAB_CHECK_ITEMS_32: List[Dict[str, str]] = [
    {"word": "borrow", "level": "A2"},
    {"word": "appointment", "level": "A2"},
    {"word": "improve", "level": "A2"},
    {"word": "convenient", "level": "A2"},
    {"word": "achieve", "level": "B1"},
    {"word": "benefit", "level": "B1"},
    {"word": "confident", "level": "B1"},
    {"word": "encourage", "level": "B1"},
    {"word": "opportunity", "level": "B1"},
    {"word": "prevent", "level": "B1"},
    {"word": "reliable", "level": "B1"},
    {"word": "accurate", "level": "B2"},
    {"word": "acquire", "level": "B2"},
    {"word": "complex", "level": "B2"},
    {"word": "considerable", "level": "B2"},
    {"word": "controversial", "level": "B2"},
    {"word": "evaluate", "level": "B2"},
    {"word": "insight", "level": "B2"},
    {"word": "justify", "level": "B2"},
    {"word": "significant", "level": "B2"},
    {"word": "strategy", "level": "B2"},
    {"word": "ambiguous", "level": "C1"},
    {"word": "compelling", "level": "C1"},
    {"word": "coherent", "level": "C1"},
    {"word": "constraint", "level": "C1"},
    {"word": "deteriorate", "level": "C1"},
    {"word": "hypothesis", "level": "C1"},
    {"word": "undermine", "level": "C1"},
    {"word": "viable", "level": "C1"},
    {"word": "arbitrary", "level": "C2"},
    {"word": "meticulous", "level": "C2"},
    {"word": "scrutinize", "level": "C2"},
]

QUICK_VOCAB_CHECK_ITEMS_48: List[Dict[str, str]] = [
    {"word": "airport", "level": "A2"},
    {"word": "borrow", "level": "A2"},
    {"word": "appointment", "level": "A2"},
    {"word": "improve", "level": "A2"},
    {"word": "convenient", "level": "A2"},
    {"word": "usual", "level": "A2"},
    {"word": "achieve", "level": "B1"},
    {"word": "attitude", "level": "B1"},
    {"word": "benefit", "level": "B1"},
    {"word": "confident", "level": "B1"},
    {"word": "effect", "level": "B1"},
    {"word": "encourage", "level": "B1"},
    {"word": "opportunity", "level": "B1"},
    {"word": "prevent", "level": "B1"},
    {"word": "reliable", "level": "B1"},
    {"word": "support", "level": "B1"},
    {"word": "accurate", "level": "B2"},
    {"word": "acquire", "level": "B2"},
    {"word": "aware", "level": "B2"},
    {"word": "complex", "level": "B2"},
    {"word": "considerable", "level": "B2"},
    {"word": "controversial", "level": "B2"},
    {"word": "demand", "level": "B2"},
    {"word": "evaluate", "level": "B2"},
    {"word": "flexible", "level": "B2"},
    {"word": "insight", "level": "B2"},
    {"word": "justify", "level": "B2"},
    {"word": "maintain", "level": "B2"},
    {"word": "significant", "level": "B2"},
    {"word": "strategy", "level": "B2"},
    {"word": "ambiguous", "level": "C1"},
    {"word": "compelling", "level": "C1"},
    {"word": "coherent", "level": "C1"},
    {"word": "constraint", "level": "C1"},
    {"word": "deteriorate", "level": "C1"},
    {"word": "emerge", "level": "C1"},
    {"word": "hypothesis", "level": "C1"},
    {"word": "incentive", "level": "C1"},
    {"word": "inevitable", "level": "C1"},
    {"word": "substantial", "level": "C1"},
    {"word": "undermine", "level": "C1"},
    {"word": "viable", "level": "C1"},
    {"word": "arbitrary", "level": "C2"},
    {"word": "conundrum", "level": "C2"},
    {"word": "meticulous", "level": "C2"},
    {"word": "ostensibly", "level": "C2"},
    {"word": "scrutinize", "level": "C2"},
    {"word": "unequivocal", "level": "C2"},
]

QUICK_VOCAB_CHECK_ITEMS_60: List[Dict[str, str]] = [
    {"word": "airport", "level": "A2"},
    {"word": "borrow", "level": "A2"},
    {"word": "cheaper", "level": "A2"},
    {"word": "noisy", "level": "A2"},
    {"word": "appointment", "level": "A2"},
    {"word": "improve", "level": "A2"},
    {"word": "convenient", "level": "A2"},
    {"word": "usual", "level": "A2"},
    {"word": "achieve", "level": "B1"},
    {"word": "attitude", "level": "B1"},
    {"word": "benefit", "level": "B1"},
    {"word": "complain", "level": "B1"},
    {"word": "confident", "level": "B1"},
    {"word": "effect", "level": "B1"},
    {"word": "encourage", "level": "B1"},
    {"word": "opportunity", "level": "B1"},
    {"word": "prevent", "level": "B1"},
    {"word": "purpose", "level": "B1"},
    {"word": "reliable", "level": "B1"},
    {"word": "support", "level": "B1"},
    {"word": "accurate", "level": "B2"},
    {"word": "acquire", "level": "B2"},
    {"word": "aware", "level": "B2"},
    {"word": "complex", "level": "B2"},
    {"word": "considerable", "level": "B2"},
    {"word": "controversial", "level": "B2"},
    {"word": "demand", "level": "B2"},
    {"word": "evaluate", "level": "B2"},
    {"word": "flexible", "level": "B2"},
    {"word": "insight", "level": "B2"},
    {"word": "justify", "level": "B2"},
    {"word": "maintain", "level": "B2"},
    {"word": "perspective", "level": "B2"},
    {"word": "priority", "level": "B2"},
    {"word": "significant", "level": "B2"},
    {"word": "strategy", "level": "B2"},
    {"word": "ambiguous", "level": "C1"},
    {"word": "compelling", "level": "C1"},
    {"word": "coherent", "level": "C1"},
    {"word": "compromise", "level": "C1"},
    {"word": "constraint", "level": "C1"},
    {"word": "deficiency", "level": "C1"},
    {"word": "deteriorate", "level": "C1"},
    {"word": "emerge", "level": "C1"},
    {"word": "hypothesis", "level": "C1"},
    {"word": "incentive", "level": "C1"},
    {"word": "inevitable", "level": "C1"},
    {"word": "notion", "level": "C1"},
    {"word": "substantial", "level": "C1"},
    {"word": "undermine", "level": "C1"},
    {"word": "valid", "level": "C1"},
    {"word": "viable", "level": "C1"},
    {"word": "arbitrary", "level": "C2"},
    {"word": "conundrum", "level": "C2"},
    {"word": "equivocal", "level": "C2"},
    {"word": "meticulous", "level": "C2"},
    {"word": "ostensibly", "level": "C2"},
    {"word": "pervasive", "level": "C2"},
    {"word": "scrutinize", "level": "C2"},
    {"word": "unequivocal", "level": "C2"},
]

# Backward-compatible alias
QUICK_VOCAB_CHECK_ITEMS = QUICK_VOCAB_CHECK_ITEMS_60


@dataclass(frozen=True)
class CheckPreset:
    size: int
    items: List[Dict[str, str]]
    solid: float
    developing: float
    weak: float
    base_confidence: float
    max_confidence: float
    allow_high_label: bool


CHECK_PRESETS: Dict[int, CheckPreset] = {
    32: CheckPreset(
        size=32,
        items=QUICK_VOCAB_CHECK_ITEMS_32,
        solid=70,
        developing=50,
        weak=30,
        base_confidence=78,
        max_confidence=85,
        allow_high_label=False,
    ),
    48: CheckPreset(
        size=48,
        items=QUICK_VOCAB_CHECK_ITEMS_48,
        solid=72,
        developing=52,
        weak=32,
        base_confidence=84,
        max_confidence=90,
        allow_high_label=True,
    ),
    60: CheckPreset(
        size=60,
        items=QUICK_VOCAB_CHECK_ITEMS_60,
        solid=75,
        developing=55,
        weak=35,
        base_confidence=90,
        max_confidence=95,
        allow_high_label=True,
    ),
}

_WORD_TO_CEFR: Dict[str, str] = {}
for _preset in CHECK_PRESETS.values():
    for _item in _preset.items:
        _WORD_TO_CEFR[_item["word"].lower()] = _item["level"]


def normalize_check_size(value: Any, *, fallback: int = 32) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return fallback
    return size if size in CHECK_PRESETS else fallback


def get_check_preset(check_size: Any) -> CheckPreset:
    return CHECK_PRESETS[normalize_check_size(check_size)]


def quick_vocab_word_id(word: str) -> str:
    return f"qvc:{word.lower()}"


def calibration_words_payload(check_size: int = 32) -> Dict[str, Any]:
    preset = get_check_preset(check_size)
    words_out: List[Dict[str, Any]] = []
    for item in preset.items:
        word = item["word"]
        words_out.append(
            {
                "word_id": quick_vocab_word_id(word),
                "word": word,
            }
        )
    random.shuffle(words_out)
    return {
        "words": words_out,
        "count": len(words_out),
        "check_size": preset.size,
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def get_band_status(score: float, preset: CheckPreset) -> BandStatus:
    if score >= preset.solid:
        return "solid"
    if score >= preset.developing:
        return "developing"
    if score >= preset.weak:
        return "weak"
    return "not-ready"


def normalize_answer_row(
    raw: Dict[str, Any], *, word_lookup: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    lookup = word_lookup or _WORD_TO_CEFR
    word = str(raw.get("word") or "").strip().lower()
    if not word:
        return None

    level = (
        raw.get("calibration_cefr_level")
        or raw.get("calibration_label")
        or raw.get("level")
    )
    if isinstance(level, int):
        level = LEVEL_INT_TO_RATING.get(max(0, min(3, level)), "New")
    if isinstance(level, str) and level.isdigit():
        level = LEVEL_INT_TO_RATING.get(int(level), "New")

    rating = raw.get("rating")
    if rating in RATING_SCORE:
        pass
    elif isinstance(raw.get("level"), int):
        rating = LEVEL_INT_TO_RATING.get(
            max(0, min(3, int(raw.get("level") or 0))), "New"
        )
    else:
        rating = "New"

    cefr: Optional[str] = None
    if isinstance(level, str) and level.upper() in CEFR_LEVELS:
        cefr = level.upper()
    if not cefr:
        cefr = lookup.get(word)
    if not cefr:
        return None

    response_ms = raw.get("response_time_ms")
    if response_ms is None:
        response_ms = raw.get("responseTimeMs")

    return {
        "word": word,
        "level": cefr,
        "rating": rating,
        "responseTimeMs": int(response_ms) if response_ms is not None else None,
    }


def calculate_band_scores(
    answers: List[Dict[str, Any]], preset: CheckPreset
) -> List[Dict[str, Any]]:
    bands: List[Dict[str, Any]] = []
    for level in CEFR_LEVELS:
        items = [a for a in answers if a["level"] == level]
        total = len(items)
        counts: Dict[str, int] = {"New": 0, "Seen": 0, "Know": 0, "Use": 0}
        for item in items:
            counts[str(item["rating"])] += 1

        raw_score = (
            0.0
            if total == 0
            else (sum(RATING_SCORE[str(i["rating"])] for i in items) / total) * 100
        )
        productive = 0.0 if total == 0 else (counts["Use"] / total) * 100

        bands.append(
            {
                "level": level,
                "total": total,
                "score": round(raw_score),
                "rawScore": raw_score,
                "productiveScore": round(productive),
                "status": get_band_status(raw_score, preset),
                "counts": counts,
            }
        )
    return bands


def estimate_vocab_level(
    answers: List[Dict[str, Any]], preset: CheckPreset
) -> Dict[str, Any]:
    """Peak reliable level = highest band marked solid (not continuous from A2)."""
    bands = calculate_band_scores(answers, preset)
    status_by_level = {b["level"]: b["status"] for b in bands}

    peak_solid_index = -1
    for index, level in enumerate(CEFR_LEVELS):
        if status_by_level[level] == "solid":
            peak_solid_index = index

    if peak_solid_index == -1:
        highest_developing_index = -1
        for index, level in enumerate(CEFR_LEVELS):
            if status_by_level[level] == "developing":
                highest_developing_index = index
        if highest_developing_index >= 0:
            base = CEFR_LEVELS[highest_developing_index]
            return {"estimatedLevel": f"early {base}", "bands": bands}
        if status_by_level.get("A2") == "developing":
            return {"estimatedLevel": "A2 developing", "bands": bands}
        return {"estimatedLevel": "Below A2", "bands": bands}

    base_level = CEFR_LEVELS[peak_solid_index]
    estimated = base_level
    next_level = (
        CEFR_LEVELS[peak_solid_index + 1]
        if peak_solid_index + 1 < len(CEFR_LEVELS)
        else None
    )
    if next_level and status_by_level[next_level] == "developing":
        estimated = f"{base_level}+"

    return {"estimatedLevel": estimated, "bands": bands}


def _inconsistency_confidence_adjustments(
    bands: List[Dict[str, Any]], *, estimated_level: str, preset: CheckPreset
) -> tuple[List[str], float]:
    flags: List[str] = []
    penalty = 0.0
    score_by_level = {b["level"]: b["score"] for b in bands}
    status_by_level = {b["level"]: b["status"] for b in bands}
    peak_base = _base_cefr_for_pack(estimated_level)
    solid_th = preset.solid

    if (
        score_by_level.get("B1", 0) >= solid_th
        and score_by_level.get("A2", 0) < solid_th
    ):
        flags.append("A2 score is lower than expected compared with B1.")
        penalty += 15

    if (
        score_by_level.get("B2", 0) >= solid_th
        and score_by_level.get("A2", 0) < solid_th
    ):
        if "A2 score is lower than expected compared with B1." not in flags:
            flags.append("A2 score is lower than expected compared with B2.")
        penalty += 10

    if peak_base.startswith("B2") and status_by_level.get("C1") in (
        "weak",
        "not-ready",
    ):
        flags.append("C1 is not ready yet.")

    if peak_base.startswith("B1") and status_by_level.get("C2") == "not-ready":
        penalty += 3

    return flags, penalty


def ielts_vocab_reference(estimated_level: str) -> str:
    if estimated_level == "Below A2":
        return "below ~4.5"
    base = _base_cefr_for_pack(estimated_level)
    ranges = {
        "A2": "~4.5–5.5",
        "B1": "~5.0–6.0",
        "B2": "~5.5–6.5",
        "C1": "~6.5–7.5",
        "C2": "~7.5–8.5",
    }
    return ranges.get(base, "~5.5–6.5")


def detect_suspicious_self_rating(
    answers: List[Dict[str, Any]], preset: CheckPreset
) -> Dict[str, Any]:
    bands = calculate_band_scores(answers, preset)
    suspicion = 0
    flags: List[str] = []
    total = len(answers)

    if total == 0:
        return {"suspicion": 0, "isSuspicious": False, "flags": flags}

    know_use = sum(1 for a in answers if a["rating"] in ("Know", "Use"))
    new_seen = sum(1 for a in answers if a["rating"] in ("New", "Seen"))
    know_use_rate = know_use / total
    new_seen_rate = new_seen / total

    if know_use_rate > 0.85 and new_seen_rate < 0.15:
        suspicion += 1
        flags.append("User marked almost everything as Know/Use.")

    score_by_level = {b["level"]: b["score"] for b in bands}
    if score_by_level.get("C1", 0) > score_by_level.get("B1", 0) + 20:
        suspicion += 1
        flags.append("C1 score is unusually higher than B1.")
    if score_by_level.get("C2", 0) > score_by_level.get("B2", 0) + 20:
        suspicion += 1
        flags.append("C2 score is unusually higher than B2.")

    hard_answers = [a for a in answers if a["level"] in ("B2", "C1", "C2")]
    hard_know_use = [a for a in hard_answers if a["rating"] in ("Know", "Use")]
    hard_fast = [
        a
        for a in hard_know_use
        if isinstance(a.get("responseTimeMs"), int) and a["responseTimeMs"] < 900
    ]
    if hard_know_use and len(hard_fast) / len(hard_know_use) > 0.35:
        suspicion += 1
        flags.append("Many hard words were marked Know/Use very quickly.")

    (bands[1]["counts"]["Use"] / bands[1]["total"]) if bands[1]["total"] else 0
    use_c1 = (bands[3]["counts"]["Use"] / bands[3]["total"]) if bands[3]["total"] else 0
    use_c2 = (bands[4]["counts"]["Use"] / bands[4]["total"]) if bands[4]["total"] else 0
    if use_c1 + use_c2 > 0.8 and score_by_level.get("B1", 0) < 60:
        suspicion += 2
        flags.append("High C-level Use rate does not match lower-level foundation.")

    rating_counts = {"New": 0, "Seen": 0, "Know": 0, "Use": 0}
    for answer in answers:
        rating_counts[str(answer["rating"])] += 1
    max_rating_rate = max(rating_counts.values()) / total
    if max_rating_rate > 0.9:
        suspicion += 1
        flags.append("User selected the same rating for almost all words.")

    return {"suspicion": suspicion, "isSuspicious": suspicion >= 3, "flags": flags}


def _confidence_label(score: float, preset: CheckPreset) -> str:
    if not preset.allow_high_label and score >= 75:
        return "Medium-high"
    if score >= 80:
        return "High"
    if score >= 60:
        return "Medium"
    if score >= 40:
        return "Low"
    return "Very low"


def calculate_confidence(
    answers: List[Dict[str, Any]],
    *,
    estimated_level: str,
    bands: List[Dict[str, Any]],
    preset: CheckPreset,
) -> Dict[str, Any]:
    suspicious = detect_suspicious_self_rating(answers, preset)
    inconsistency_flags, inconsistency_penalty = _inconsistency_confidence_adjustments(
        bands, estimated_level=estimated_level, preset=preset
    )

    score_by_level = {b["level"]: b["score"] for b in bands}
    solid_th = preset.solid
    a2_inconsistent = score_by_level.get("A2", 0) < solid_th and (
        score_by_level.get("B1", 0) >= solid_th
        or score_by_level.get("B2", 0) >= solid_th
    )
    developing_count = sum(
        1
        for b in bands
        if b["status"] == "developing" and not (b["level"] == "A2" and a2_inconsistent)
    )
    weak_middle = sum(
        1 for b in bands if b["level"] in ("A2", "B1", "B2") and b["status"] == "weak"
    )

    confidence = preset.base_confidence
    confidence -= developing_count * 4
    confidence -= weak_middle * 5
    confidence -= inconsistency_penalty
    confidence -= suspicious["suspicion"] * 12
    confidence = _clamp(confidence, 20, preset.max_confidence)

    label = _confidence_label(confidence, preset)
    if not preset.allow_high_label and label == "High":
        label = "Medium-high"

    merged_flags: List[str] = []
    for flag in inconsistency_flags + suspicious["flags"]:
        if flag not in merged_flags:
            merged_flags.append(flag)

    return {
        "confidence": round(confidence),
        "confidenceLabel": label,
        "flags": merged_flags,
        "isSuspicious": suspicious["isSuspicious"] or inconsistency_penalty >= 20,
    }


def get_quick_vocab_check_result(
    answers: List[Dict[str, Any]], *, check_size: int = 32
) -> Dict[str, Any]:
    preset = get_check_preset(check_size)
    lookup = {item["word"].lower(): item["level"] for item in preset.items}
    normalized = [
        row
        for row in (normalize_answer_row(a, word_lookup=lookup) for a in answers)
        if row
    ]
    level_result = estimate_vocab_level(normalized, preset)
    bands = level_result["bands"]
    estimated = str(level_result["estimatedLevel"])
    confidence_result = calculate_confidence(
        normalized, estimated_level=estimated, bands=bands, preset=preset
    )

    return {
        "estimatedLevel": estimated,
        "confidence": confidence_result["confidence"],
        "confidenceLabel": confidence_result["confidenceLabel"],
        "isSuspicious": confidence_result["isSuspicious"],
        "flags": confidence_result["flags"],
        "bands": bands,
        "check_size": preset.size,
    }


def _base_cefr_for_pack(estimated_level: str) -> str:
    if estimated_level == "Below A2":
        return "A2"
    if estimated_level.startswith("early "):
        return estimated_level.replace("early ", "", 1)
    if estimated_level.endswith(" developing"):
        return estimated_level.replace(" developing", "", 1)
    return estimated_level.rstrip("+")


def _build_summary_copy(
    *,
    estimated_level: str,
    bands: List[Dict[str, Any]],
    locale: str,
    preset: CheckPreset,
) -> tuple[str, str, List[str], List[str]]:
    vi = str(locale).lower().startswith("vi")
    peak = _base_cefr_for_pack(estimated_level)
    score_by_level = {b["level"]: b["score"] for b in bands}
    status_by_level = {b["level"]: b["status"] for b in bands}
    solid_th = preset.solid

    if preset.size == 32:
        headline = "Initial vocab estimate" if not vi else "Ước lượng từ vựng ban đầu"
    else:
        headline = (
            f"Estimated vocab level: {estimated_level}"
            if not vi
            else f"Mức từ vựng ước lượng: {estimated_level}"
        )

    parts: List[str] = []
    a2_below_peak = (
        peak in ("B1", "B2", "C1", "C2") and score_by_level.get("A2", 0) < solid_th
    )

    if estimated_level == "Below A2":
        parts.append(
            "Kết quả cho thấy nền từ vựng còn mỏng — nên bắt đầu từ pack A2 và ôn recall thường xuyên."
            if vi
            else "Your foundation still looks thin — start with A2 packs and steady recall."
        )
    elif a2_below_peak and peak in ("B1", "B2"):
        parts.append(
            "Bạn có vốn từ B1–B2 khá tốt."
            if vi
            else "You seem comfortable with many B1–B2 words."
        )
        parts.append(
            "Tuy nhiên, điểm A2 thấp hơn kỳ vọng so với B1/B2 — có thể bạn đánh giá chưa nhất quán ở một số từ cơ bản."
            if vi
            else (
                "However, your A2 score is lower than expected for your B1/B2 results — "
                "some basic words may have been rated inconsistently."
            )
        )
    else:
        peak_band = next((b for b in bands if b["level"] == peak), None)
        if peak_band and peak_band["status"] == "solid":
            parts.append(
                f"Bạn khá vững ở {peak}."
                if vi
                else f"You seem solid with {peak} vocabulary."
            )
        elif peak_band and peak_band["status"] == "developing":
            parts.append(
                f"Bạn đang phát triển ở {peak}."
                if vi
                else f"You are developing at the {peak} band."
            )

    if peak.startswith("B2") and status_by_level.get("C1") in ("weak", "not-ready"):
        parts.append(
            "Bạn chưa sẵn sàng cho C1 — ưu tiên củng cố B2 trước."
            if vi
            else "C1 is not ready yet — strengthen B2 vocabulary first."
        )
    elif peak == "B1" and status_by_level.get("B2") == "developing":
        parts.append(
            "Bạn đang bước sang B2 — tiếp tục luyện từ B2 trong câu ngắn."
            if vi
            else "You are starting to recognize some B2 words."
        )

    if preset.size == 32:
        parts.append(
            "Đây là ước lượng nhanh — app sẽ điều chỉnh level khi bạn học thêm."
            if vi
            else "This is a quick estimate — we will adjust your level as you learn."
        )

    peak_band = next((b for b in bands if b["level"] == peak), None)
    if peak_band and peak_band["productiveScore"] < peak_band["score"] - 15:
        parts.append(
            "Bạn nhận biết nhiều từ hơn mức bạn đang dùng được — hãy luyện viết câu ngắn."
            if vi
            else (
                "You recognize more words than you use actively — "
                "practice short sentences with target words."
            )
        )

    summary = (
        " ".join(parts)
        if parts
        else (
            "Hoàn tất quick check — bắt đầu pack phù hợp và ôn từ chưa chắc."
            if vi
            else "Quick check complete — start a matching pack and repair uncertain words."
        )
    )

    strengths: List[str] = []
    weak_spots: List[str] = []
    for band in bands:
        if band["status"] == "solid":
            strengths.append(
                f"{band['level']}: {band['score']}% recognition "
                f"({band['productiveScore']}% active use)"
            )
        elif band["status"] in ("weak", "not-ready"):
            weak_spots.append(
                f"{band['level']}: cần củng cố thêm"
                if vi
                else f"{band['level']}: needs reinforcement"
            )

    return headline, summary, strengths[:3], weak_spots[:3]


def build_calibration_review(
    answers: List[Dict[str, Any]],
    *,
    locale: str = "vi",
    packs: Optional[List[Dict[str, Any]]] = None,
    check_size: int = 32,
) -> Dict[str, Any]:
    from aiforen.domain.vocab_calibration_cefr import pick_recommended_pack_id

    preset = get_check_preset(check_size)
    packs = packs or []
    result = get_quick_vocab_check_result(answers, check_size=preset.size)
    estimated = str(result["estimatedLevel"])
    base_cefr = _base_cefr_for_pack(estimated)
    ielts_ref = ielts_vocab_reference(estimated)

    headline, summary, strengths, weak_spots = _build_summary_copy(
        estimated_level=estimated,
        bands=result["bands"],
        locale=locale,
        preset=preset,
    )

    vi = str(locale).lower().startswith("vi")
    recommended_plan = [
        {
            "title": (
                f"Bắt đầu pack {base_cefr}" if vi else f"Start {base_cefr} vocabulary"
            ),
            "description": (
                "Học từ theo level ước lượng, ưu tiên recall chủ động."
                if vi
                else "Study words at your estimated level with active recall."
            ),
        },
        {
            "title": "Luyện câu ngắn" if vi else "Short sentence practice",
            "description": (
                "Chuyển từ Know sang Use bằng câu IELTS-style."
                if vi
                else "Move Know → Use with short IELTS-style sentences."
            ),
        },
    ]

    return {
        "headline": headline,
        "summary": summary,
        "estimated_vocab_level": estimated,
        "cefr_level": base_cefr,
        "ielts_vocab_ref": ielts_ref,
        "estimated_band": None,
        "ielts_band_hint": None,
        "confidence": float(result["confidence"]) / 100.0,
        "confidence_label": result["confidenceLabel"],
        "is_suspicious": result["isSuspicious"],
        "flags": result.get("flags") or [],
        "bands": result["bands"],
        "strengths": strengths,
        "weak_spots": weak_spots,
        "recommended_plan": recommended_plan,
        "recommended_pack_id": pick_recommended_pack_id(base_cefr, packs),
        "check_size": preset.size,
        "source": "quick_vocab_check_rules",
    }
