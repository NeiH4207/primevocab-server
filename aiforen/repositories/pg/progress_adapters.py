"""Map Postgres learner rows to legacy Mongo-shaped progress dicts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from aiforen.domain.sql_models import GrammarLearningProgress, VocabUserWordState


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _json_safe(value: Any) -> Any:
    """Make nested progress dicts JSON-serializable for JSONB columns."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _parse_dt(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def word_state_to_progress(row: VocabUserWordState) -> Dict[str, Any]:
    doc = dict(row.progress_data or {})
    doc.setdefault("user_id", str(row.user_id))
    doc.setdefault("content_id", row.word_id)
    doc.setdefault("content_type", "vocabulary")
    doc.setdefault("pack_id", row.pack_id)
    doc["mastery_level"] = row.mastery_level
    doc["mastery_step"] = int(row.mastery_step or 0)
    doc["mastery_point_pct"] = float(row.mastery_point_pct or 0)
    doc["marked_known"] = bool(row.marked_known)
    doc["best_translate_pct"] = float(row.best_translate_pct or 0)
    doc["best_topic_pct"] = float(row.best_topic_pct or 0)
    if row.due_at and "spaced_repetition" not in doc:
        doc["spaced_repetition"] = {"next_review": row.due_at}
    elif row.due_at and isinstance(doc.get("spaced_repetition"), dict):
        doc["spaced_repetition"]["next_review"] = row.due_at
    if row.first_studied_at:
        doc.setdefault("first_studied", row.first_studied_at)
    if row.last_studied_at:
        doc.setdefault("last_studied", row.last_studied_at)
    doc["updated_at"] = row.updated_at
    return doc


def progress_to_word_state_values(
    user_id: str | uuid.UUID,
    word_id: str,
    doc: Dict[str, Any],
    *,
    lexeme_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    safe_doc = _json_safe(doc)
    sr = safe_doc.get("spaced_repetition") or {}
    next_review = _parse_dt(sr.get("next_review"))
    return {
        "user_id": _as_uuid(user_id),
        "word_id": word_id,
        "lexeme_id": lexeme_id,
        "pack_id": doc.get("pack_id"),
        "mastery_level": doc.get("mastery_level") or "new",
        "mastery_step": int(doc.get("mastery_step") or 0),
        "mastery_point_pct": float(doc.get("mastery_point_pct") or 0),
        "due_at": next_review if isinstance(next_review, datetime) else None,
        "failed_locked_until": _parse_dt(doc.get("failed_locked_until")),
        "marked_known": bool(doc.get("marked_known")),
        "best_translate_pct": float(doc.get("best_translate_pct") or 0),
        "best_topic_pct": float(doc.get("best_topic_pct") or 0),
        "last_result": {
            "last_mcq_result": safe_doc.get("last_mcq_result"),
            "last_sentence_id": safe_doc.get("last_sentence_id"),
        },
        "progress_data": safe_doc,
        "weakness_tags": list(safe_doc.get("weakness_tags") or []),
        "first_studied_at": _parse_dt(safe_doc.get("first_studied")),
        "last_studied_at": _parse_dt(
            safe_doc.get("last_studied") or safe_doc.get("updated_at")
        ),
    }


def grammar_progress_to_dict(row: GrammarLearningProgress) -> Dict[str, Any]:
    doc = dict(row.progress_data or {})
    doc.setdefault("user_id", str(row.user_id))
    doc.setdefault("content_id", row.structure_id)
    doc.setdefault("content_type", "grammar")
    doc["mastery_level"] = row.mastery_level
    if row.last_studied_at:
        doc.setdefault("last_studied", row.last_studied_at)
    doc["updated_at"] = row.updated_at
    return doc
