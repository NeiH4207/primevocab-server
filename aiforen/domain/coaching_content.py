"""Level-based reading content catalog resolver."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Union

from aiforen.domain.sql_models import CoachingReadingUnit, CoachingReadingUnitQuestion

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")

READING_QUESTION_TYPES = (
    "comprehension",
    "true_false",
    "gap_fill",
    "vocabulary",
    "phrase_choice",
)


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z'-]", "", (value or "").strip().lower())


def passage_tokens_from_paragraphs(paragraphs: Sequence[str]) -> List[str]:
    """Unique lowercase word tokens in passage order."""
    seen: set[str] = set()
    out: List[str] = []
    text = "\n\n".join(str(p) for p in paragraphs)
    for match in _TOKEN_RE.finditer(text):
        token = normalize_token(match.group(0))
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _question_row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def normalize_question(
    row: Union[CoachingReadingUnitQuestion, Dict[str, Any], Any],
) -> Dict[str, Any]:
    if isinstance(row, CoachingReadingUnitQuestion):
        qtype = row.question_type
        options = list(row.options or [])
        acceptable = list(row.acceptable_answers or [])
        return {
            "id": row.id,
            "type": qtype,
            "question_type": qtype,
            "prompt": row.prompt,
            "options": options,
            "correct_option": row.correct_option,
            "acceptable_answers": acceptable,
            "explanation": row.explanation,
            "source_word": row.source_word,
        }
    qtype = str(
        _question_row_value(row, "question_type")
        or _question_row_value(row, "type")
        or "comprehension"
    )
    options = list(_question_row_value(row, "options") or [])
    acceptable = list(_question_row_value(row, "acceptable_answers") or [])
    return {
        "id": str(_question_row_value(row, "id") or ""),
        "type": qtype,
        "question_type": qtype,
        "prompt": str(_question_row_value(row, "prompt") or ""),
        "options": options,
        "correct_option": str(_question_row_value(row, "correct_option") or ""),
        "acceptable_answers": acceptable,
        "explanation": _question_row_value(row, "explanation"),
        "source_word": _question_row_value(row, "source_word"),
    }


def unit_to_reading_payload(
    unit: CoachingReadingUnit,
    difficult_words: List[Dict[str, Any]],
) -> Dict[str, Any]:
    questions = [
        normalize_question(q)
        for q in sorted(unit.questions or [], key=lambda item: item.sort_order)
    ]
    return {
        "id": unit.id,
        "content_unit_id": unit.id,
        "content_version": unit.content_version,
        "target_cefr": unit.cefr_level,
        "topic_slug": unit.topic_slug,
        "topic_title": unit.topic_title,
        "title": unit.title,
        "source_label": unit.source_label,
        "estimated_minutes": unit.estimated_minutes,
        "paragraphs": list(unit.paragraphs or []),
        "difficult_words": difficult_words,
        "questions": questions,
        "question_limit": unit.question_limit,
        "vocab_keyword_seeds": list(getattr(unit, "vocab_keywords", None) or []),
        "vocab_candidates": [],
        "placeholder": False,
    }


def placeholder_reading(cefr_level: str, day_number: int) -> Dict[str, Any]:
    level = (cefr_level or "B1").upper()
    slug = f"{level.lower()}-day{day_number:02d}-placeholder"
    return {
        "id": slug,
        "content_unit_id": None,
        "content_version": 0,
        "target_cefr": level,
        "topic_slug": "coming-soon",
        "topic_title": "Content coming soon",
        "title": "Reading content coming soon",
        "source_label": f"PrimeVocab Original · {level}",
        "estimated_minutes": 0,
        "paragraphs": [],
        "difficult_words": [],
        "questions": [],
        "question_limit": 0,
        "vocab_candidates": [],
        "placeholder": True,
    }


def grade_reading_answer(question: Dict[str, Any], selected: str) -> bool:
    qtype = str(
        question.get("question_type") or question.get("type") or "comprehension"
    )
    answer = (selected or "").strip()
    correct = str(question.get("correct_option") or "").strip()

    if qtype == "gap_fill":
        normalized = answer.lower()
        acceptable = question.get("acceptable_answers") or []
        if acceptable:
            return any(
                normalized == str(item or "").strip().lower() for item in acceptable
            )
        return normalized == correct.lower()

    if answer == correct:
        return True

    options = question.get("options") or []
    if len(correct) == 1 and correct.isalpha() and options:
        idx = ord(correct.upper()) - ord("A")
        if 0 <= idx < len(options) and answer == options[idx]:
            return True
    return False


def should_refresh_reading_snapshot(
    reading: Dict[str, Any],
    unit: CoachingReadingUnit,
    *,
    reading_answers: Dict[str, Any],
    reading_status: Optional[str],
) -> bool:
    if not reading or reading.get("placeholder"):
        return True
    if int(reading.get("content_version") or 0) >= int(unit.content_version or 0):
        return False
    if reading_answers:
        return False
    if reading_status == "completed":
        return False
    return True
