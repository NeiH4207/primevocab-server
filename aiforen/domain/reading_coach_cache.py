"""Cache key helpers for shared Reading Coach helper-note cards."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional, Tuple

from aiforen.domain.vocab_coaching_reading import normalize_token

READING_COACH_PROMPT_VERSION = "v3"

_MOCK_MAIN_NOTE_MARKERS = (
    "trong câu đang đọc, ưu tiên cụm",
    "Explain «",
    "using a real chunk when possible",
)

_PLACEHOLDER_MEANINGS = {
    "nghĩa theo ngữ cảnh",
    "meaning in context",
}


def normalize_sentence_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_locale(locale: str) -> str:
    return "vi" if str(locale or "").lower().startswith("vi") else "en"


def normalize_user_level(value: str) -> str:
    cleaned = (value or "B1").strip().upper()
    return cleaned[:8] if cleaned else "B1"


def resolve_reading_id(reading: Dict[str, Any]) -> str:
    rid = str(reading.get("id") or "").strip()
    if rid:
        return rid
    title = str(reading.get("title") or "").strip()
    if title:
        return hashlib.sha256(title.encode("utf-8")).hexdigest()[:32]
    return "unknown"


def build_reading_coach_cache_key(
    *,
    reading_id: str,
    selection_type: str,
    selected_text: str,
    sentence_text: str,
    locale: str,
    user_level: str,
    model_name: str,
    prompt_version: str = READING_COACH_PROMPT_VERSION,
) -> Tuple[str, Dict[str, str]]:
    sel_type = str(selection_type or "word").strip().lower()
    if sel_type not in ("word", "sentence"):
        sel_type = "word"

    selected = normalize_sentence_text(selected_text)
    sentence = normalize_sentence_text(sentence_text)

    if sel_type == "word":
        first = selected.split()[0] if selected.split() else selected
        target_norm = normalize_token(first) or normalize_token(selected)
    else:
        target_norm = sentence

    parts = {
        "reading_id": str(reading_id or "unknown").strip(),
        "selection_type": sel_type,
        "target_norm": target_norm,
        "sentence_norm": sentence,
        "locale": normalize_locale(locale),
        "user_level": normalize_user_level(user_level),
        "prompt_version": str(prompt_version or READING_COACH_PROMPT_VERSION),
        "model_name": str(model_name or "").strip(),
    }
    digest = hashlib.sha256(
        json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:64]
    return digest, parts


def cache_key_from_selection(
    *,
    reading: Dict[str, Any],
    reading_selection: Optional[Dict[str, Any]],
    locale: str,
    user_level: str,
    model_name: str,
) -> Optional[Tuple[str, Dict[str, str]]]:
    """Return (cache_key, parts) when selection is cacheable; else None."""
    sel = reading_selection or {}
    selection_type = str(sel.get("selection_type") or "").strip().lower()
    selected_text = str(sel.get("selected_text") or "").strip()
    if selection_type not in ("word", "sentence") or not selected_text:
        return None

    sentence_text = str(sel.get("sentence_text") or selected_text).strip()
    reading_id = resolve_reading_id(reading)
    level = str(sel.get("user_level") or user_level or "B1").strip()

    cache_key, parts = build_reading_coach_cache_key(
        reading_id=reading_id,
        selection_type=selection_type,
        selected_text=selected_text,
        sentence_text=sentence_text,
        locale=locale,
        user_level=level,
        model_name=model_name,
    )
    return cache_key, parts


def is_cacheable_reading_coach_card(card: Dict[str, Any]) -> bool:
    """Reject mock placeholders and empty cards."""
    if not card or not card.get("should_show"):
        return False

    main_note = str(
        card.get("main_note") or card.get("mainNoteVi") or card.get("guide") or ""
    ).strip()
    if not main_note:
        return False

    for marker in _MOCK_MAIN_NOTE_MARKERS:
        if marker in main_note:
            return False

    meaning_plain = ""
    mic = card.get("meaning_in_context")
    if isinstance(mic, dict):
        meaning_plain = str(mic.get("plain") or "").strip()
    if not meaning_plain:
        meaning_plain = str(card.get("meaning") or "").strip()

    if meaning_plain in _PLACEHOLDER_MEANINGS and any(
        marker in main_note for marker in _MOCK_MAIN_NOTE_MARKERS
    ):
        return False

    return True
