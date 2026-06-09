"""Load coaching reading units from vocab_storage schema v2 JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

_DEFAULT_STORAGE = Path(__file__).resolve().parents[3] / "vocab_storage"


def _resolve_storage_root() -> Path:
    raw = os.environ.get("VOCAB_STORAGE_DIR", "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_STORAGE


def storage_reading_dir() -> Path:
    return _resolve_storage_root() / "coaching_reading"


_COMPREHENSION_TYPES = frozenset(
    {
        "detail_mcq",
        "main_idea_mcq",
        "cause_effect_mcq",
        "inference_mcq",
        "writer_purpose_mcq",
        "writer_agreement_mcq",
        "paragraph_function_mcq",
        "comprehension",
    }
)
_VOCABULARY_TYPES = frozenset(
    {
        "vocab_meaning_mcq",
        "vocab_in_context_mcq",
        "vocabulary",
    }
)


def editorial_question_type_to_db(question_type: str) -> str:
    qtype = (question_type or "detail_mcq").strip()
    if qtype == "gap_fill":
        return "gap_fill"
    if qtype in _VOCABULARY_TYPES:
        return "vocabulary"
    if qtype in _COMPREHENSION_TYPES:
        return "comprehension"
    return "comprehension"


def load_v2_unit(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_v2_unit_paths() -> List[Path]:
    reading_dir = storage_reading_dir()
    if not reading_dir.is_dir():
        return []
    return sorted(reading_dir.glob("*.json"))


def paragraph_texts(unit: Dict[str, Any]) -> List[str]:
    paragraphs = unit.get("paragraphs") or []
    if not paragraphs:
        return []
    if isinstance(paragraphs[0], str):
        return list(paragraphs)
    return [
        str(p.get("text") or "")
        for p in sorted(paragraphs, key=lambda x: x.get("sort_order", 0))
    ]


def vocab_keywords_from_unit(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = unit.get("target_vocabulary") or unit.get("vocab_keywords") or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lemma = str(row.get("lemma") or row.get("word") or "").strip()
        if not lemma:
            continue
        out.append(
            {
                "lemma": lemma,
                "pos": row.get("pos"),
                "vi_gloss": row.get("vi_gloss"),
            }
        )
    return out


def question_to_db_row(question: Dict[str, Any]) -> Dict[str, Any]:
    qtype = str(question.get("question_type") or "detail_mcq")
    db_type = editorial_question_type_to_db(qtype)

    if db_type == "gap_fill":
        correct = str(
            question.get("correct_answer") or question.get("correct_option") or ""
        )
        return {
            "id": question["id"],
            "sort_order": int(question["sort_order"]),
            "question_type": "gap_fill",
            "prompt": str(question["prompt"]),
            "options": [],
            "correct_option": correct,
            "acceptable_answers": list(question.get("acceptable_answers") or [correct]),
            "explanation": question.get("explanation"),
            "source_word": question.get("source_word"),
        }

    options = question.get("options") or []
    if options and isinstance(options[0], dict):
        correct_id = str(question.get("correct_option_id") or "")
        option_texts = [str(o.get("text") or "") for o in options]
        correct_text = next(
            (
                str(o.get("text") or "")
                for o in options
                if str(o.get("id")) == correct_id
            ),
            str(question.get("correct_option") or ""),
        )
    else:
        option_texts = [str(o) for o in options]
        correct_text = str(question.get("correct_option") or "")

    return {
        "id": question["id"],
        "sort_order": int(question["sort_order"]),
        "question_type": db_type,
        "prompt": str(question["prompt"]),
        "options": option_texts,
        "correct_option": correct_text,
        "acceptable_answers": list(question.get("acceptable_answers") or []),
        "explanation": question.get("explanation"),
        "source_word": question.get("source_word"),
    }


def unit_to_upsert_payload(unit: Dict[str, Any]) -> Dict[str, Any]:
    topic = unit.get("topic") or {}
    topic_slug = str(
        topic.get("subtopic_slug")
        or topic.get("topic_slug")
        or unit.get("topic_slug")
        or ""
    )
    topic_title = str(
        topic.get("topic_title") or unit.get("topic_title") or unit.get("title") or ""
    )
    questions = [
        question_to_db_row(q)
        for q in sorted(
            unit.get("questions") or [], key=lambda x: x.get("sort_order", 0)
        )
    ]
    return {
        "unit_id": unit["id"],
        "cefr_level": unit["cefr_level"],
        "day_number": int(unit["day_number"]),
        "topic_slug": topic_slug,
        "topic_title": topic_title,
        "title": unit["title"],
        "paragraphs": paragraph_texts(unit),
        "source_label": unit["source_label"],
        "estimated_minutes": int(unit.get("estimated_minutes") or 8),
        "question_limit": len(questions),
        "content_version": int(unit.get("content_version") or 1),
        "status": unit.get("status") or "published",
        "questions": questions,
        "vocab_keywords": vocab_keywords_from_unit(unit),
    }


def load_all_v2_upsert_payloads() -> List[Dict[str, Any]]:
    return [unit_to_upsert_payload(load_v2_unit(path)) for path in list_v2_unit_paths()]
