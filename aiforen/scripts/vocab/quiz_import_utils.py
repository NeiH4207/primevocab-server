"""Shared helpers for importing quiz_*_vocab.json into vocab_questions."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Legacy aliases from older pipelines; default is keep canonical task_type from JSON.
QUIZ_TASK_ALIASES: Dict[str, str] = {
    "meaning_in_context": "meaning_mcq",
    "gre_sentence_completion": "gre_completion",
}

ACTIVE_STATUSES = frozenset({"validated", "approved", "generated"})


def canonical_task_type(raw: str) -> str:
    key = (raw or "").strip()
    if not key:
        return ""
    return QUIZ_TASK_ALIASES.get(key, key)


def track_id_from_level(level_code: str) -> str:
    lc = (level_code or "B1").strip().upper()
    if lc == "IELTS":
        return "ielts:core"
    if lc == "GRE":
        return "gre:core"
    return f"cefr:{lc}"


def skill_for_task(task_type: str, raw_skill: Optional[str] = None) -> str:
    if raw_skill and str(raw_skill).strip():
        return str(raw_skill).strip()
    return {
        "vn_meaning_mcq": "meaning",
        "meaning_mcq": "meaning",
        "meaning_in_context": "meaning",
        "simple_cloze": "context",
        "pattern_cloze": "pattern",
        "cloze": "pattern",
        "collocation_mcq": "collocation",
        "collocation": "collocation",
        "error_diagnosis": "error_diagnosis",
        "usage_fix": "error_diagnosis",
        "sentence_reorder": "syntax",
        "translate_with_hints": "production",
        "guided_rewrite": "production",
        "nuance_in_context": "nuance",
        "academic_collocation": "academic_collocation",
        "register_choice": "register",
        "precision_in_context": "precision",
        "precision_cloze": "precision",
        "register_tone_judgment": "register",
        "ielts_topic_meaning_mcq": "topic_meaning",
        "ielts_collocation_cloze": "academic_collocation",
        "ielts_paraphrase_recognition": "paraphrase",
        "gre_precision_definition": "precise_meaning",
        "gre_logic_contrast": "semantic_logic",
        "gre_text_completion": "text_completion",
        "gre_sentence_equivalence": "sentence_equivalence",
        "gre_completion": "text_completion",
        "paraphrase": "paraphrase",
    }.get(task_type, "meaning")


def map_question_status(raw: Optional[str]) -> str:
    if raw in ("approved", "validated"):
        return "approved"
    if raw in ("rejected", "archived"):
        return "rejected"
    return "generated"


def wire_options(options: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], str]:
    wired: List[Dict[str, str]] = []
    correct = "a"
    for opt in options or []:
        oid = str(opt.get("id") or "").strip().lower() or "a"
        if len(oid) > 1 and oid.isalpha():
            oid = oid[0]
        text = str(opt.get("text") or "").strip()
        wired.append({"id": oid, "text": text})
        if opt.get("is_correct"):
            correct = oid
    if not wired:
        wired = [{"id": "a", "text": ""}]
    return wired, correct


def question_prompt(q: Dict[str, Any]) -> str:
    prompt = (q.get("prompt") or "").strip()
    context = (q.get("context") or "").strip()
    if context and context not in prompt:
        return f"{prompt}\n\n{context}" if prompt else context
    return prompt or "Choose the best answer."


def question_row_from_quiz(
    q: Dict[str, Any],
    *,
    lexeme_id: str,
    sense_id: Optional[str],
    track_id: str,
    level_code: str,
    storage_file: str,
) -> Optional[Tuple[Any, ...]]:
    """Build INSERT tuple for vocab_questions (production v2 schema)."""
    interaction = (q.get("interaction_kind") or "mcq").strip().lower()
    raw_type = (q.get("task_type") or "").strip()
    task_type = canonical_task_type(raw_type)
    if not task_type:
        return None

    slot = max(1, min(5, int(q.get("mastery_slot") or 1)))
    skill = skill_for_task(task_type, q.get("skill"))
    status = map_question_status(q.get("status"))
    payload = dict(q.get("payload") or {})
    if q.get("context"):
        payload.setdefault("context", q.get("context"))

    if interaction == "mcq":
        options, correct = wire_options(q.get("options") or [])
        prompt = question_prompt(q)
    else:
        options = []
        correct = str(q.get("correct_option_id") or "").strip() or "n/a"
        prompt = (q.get("prompt") or "").strip() or task_type.replace("_", " ").title()

    meta = {
        "source": "vocab_storage",
        "storage_question_id": q.get("question_id"),
        "storage_file": storage_file,
        "raw_task_type": raw_type,
        "mastery_slot": slot,
        "track_level": level_code,
    }

    return (
        str(uuid.uuid4()),
        lexeme_id,
        sense_id,
        track_id,
        task_type,
        skill,
        level_code,
        slot,
        interaction,
        prompt,
        json.dumps(options),
        correct[:8] if correct else "n/a",
        q.get("explanation"),
        slot,
        status,
        json.dumps(payload),
        json.dumps(meta),
    )
