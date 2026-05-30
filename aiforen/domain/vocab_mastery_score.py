"""Pack mastery scoring: word budget 100/N split by step weights (sum=1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Step weights must sum to 1.0 (share of each word's 100/N budget).
W_LEARN = 0.08
W_MCQ = 0.22
W_TRANSLATE = 0.45
W_TOPIC = 0.25

# Five-slot CEFR quiz matrix (3× MCQ + reorder + free_text) shares W_MCQ per word cycle.
QUIZ_MATRIX_SLOTS = 5

assert abs(W_LEARN + W_MCQ + W_TRANSLATE + W_TOPIC - 1.0) < 1e-9

EN_WORD_RE = re.compile(r"[A-Za-z']+")
VI_WORD_RE = re.compile(r"\w+", re.UNICODE)
TARGET_RE_CACHE: dict[str, re.Pattern[str]] = {}

DECAY_FACTOR_PER_DAY = 0.99


@dataclass(frozen=True)
class ProductionScore:
    delta_pct: float
    quality: float
    passed: bool
    reason: str = ""


def word_budget_pct(pack_total_words: int) -> float:
    """Percent of pack mastery one word can contribute at full cycle (quality=1)."""
    return 100.0 / max(1, pack_total_words)


def count_english_words(sentence: str) -> int:
    return len(EN_WORD_RE.findall(sentence or ""))


def count_vietnamese_words(prompt: str) -> int:
    return len(VI_WORD_RE.findall(prompt or ""))


def _target_pattern(target_word: str) -> re.Pattern[str]:
    key = (target_word or "").strip().lower()
    if key not in TARGET_RE_CACHE:
        escaped = re.escape(key)
        TARGET_RE_CACHE[key] = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return TARGET_RE_CACHE[key]


def sentence_contains_target(sentence: str, target_word: str) -> bool:
    word = (target_word or "").strip()
    if not word:
        return False
    return bool(_target_pattern(word).search(sentence or ""))


def delta_learn(pack_total_words: int) -> float:
    return word_budget_pct(pack_total_words) * W_LEARN


def delta_mcq(pack_total_words: int) -> float:
    return word_budget_pct(pack_total_words) * W_MCQ


def delta_quiz_slot(pack_total_words: int, *, slots: int = QUIZ_MATRIX_SLOTS) -> float:
    """Mastery credit for one quiz-matrix slot (split W_MCQ across slots)."""
    return delta_mcq(pack_total_words) / max(1, slots)


def score_translate(
    *,
    sentence: str,
    target_word: str,
    vi_prompt: str,
    pack_total_words: int,
    looks_like_vietnamese: bool = False,
) -> ProductionScore:
    """Translate step: gates + quality; delta = word_budget * w3 * quality."""
    budget = word_budget_pct(pack_total_words)
    n = count_english_words(sentence)
    len_vi = count_vietnamese_words(vi_prompt)

    if looks_like_vietnamese:
        return ProductionScore(0.0, 0.0, False, "vietnamese")
    if not sentence_contains_target(sentence, target_word):
        return ProductionScore(0.0, 0.0, False, "missing_target_word")
    if n < 3:
        return ProductionScore(0.0, 0.0, False, "too_short")
    if len_vi > 0 and n > 3 * len_vi:
        return ProductionScore(0.0, 0.0, False, "too_long")

    quality = max(1.0, (n - 3) / max(1, len_vi))
    delta = budget * W_TRANSLATE * quality
    return ProductionScore(delta, quality, True)


def score_topic(
    *,
    sentence: str,
    target_word: str,
    pack_total_words: int,
    looks_like_vietnamese: bool = False,
) -> ProductionScore:
    """Topic step: gates + quality; delta = word_budget * w4 * quality."""
    budget = word_budget_pct(pack_total_words)
    n = count_english_words(sentence)

    if looks_like_vietnamese:
        return ProductionScore(0.0, 0.0, False, "vietnamese")
    if not sentence_contains_target(sentence, target_word):
        return ProductionScore(0.0, 0.0, False, "missing_target_word")
    if n < 3:
        return ProductionScore(0.0, 0.0, False, "too_short")

    quality = max(1.0, (n - 3) / 10.0)
    delta = budget * W_TOPIC * quality
    return ProductionScore(delta, quality, True)


def apply_calendar_decay(
    pct: float,
    last_decay_date: Optional[str],
    today: Optional[str] = None,
) -> tuple[float, str]:
    """Apply 1% decay per calendar day since last_decay_date (VN date keys)."""
    today_key = today or date.today().isoformat()
    if not last_decay_date:
        return pct, today_key
    try:
        last_d = date.fromisoformat(last_decay_date[:10])
        cur_d = date.fromisoformat(today_key[:10])
    except ValueError:
        return pct, today_key
    days = max(0, (cur_d - last_d).days)
    if days <= 0:
        return pct, today_key
    return pct * (DECAY_FACTOR_PER_DAY**days), today_key


def display_step_for_word(
    mastery_point_pct: float,
    pack_total_words: int,
) -> int:
    """0..5 dots from fraction of word_budget earned."""
    budget = word_budget_pct(pack_total_words)
    if budget <= 0:
        return 0
    filled = mastery_point_pct / budget
    return min(5, max(0, int(round(filled * 5))))


def migrate_legacy_word_points(
    progress: dict,
    pack_total_words: int,
) -> float:
    """Approximate mastery_point_pct from legacy boolean flags."""
    if progress.get("marked_known") or progress.get("mastery_level") == "mastered":
        return word_budget_pct(pack_total_words)
    budget = word_budget_pct(pack_total_words)
    pts = 0.0
    if progress.get("learn_passed"):
        pts += budget * W_LEARN
    if (progress.get("last_mcq_result") or {}).get("is_correct"):
        pts += budget * W_MCQ
    if progress.get("translate_passed"):
        pts += budget * W_TRANSLATE
    if progress.get("topic_passed"):
        pts += budget * W_TOPIC
    try:
        step = int(progress.get("mastery_step", 0))
    except (TypeError, ValueError):
        step = 0
    if step > 0 and not progress.get("translate_passed"):
        pts += budget * W_TRANSLATE * min(1.0, step / 5.0)
    return min(budget * 2.0, pts)
