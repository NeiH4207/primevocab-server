"""Deterministic CEFR workout prescription helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

CANONICAL_SKILLS = frozenset(
    {
        "meaning",
        "context",
        "collocation",
        "pattern",
        "translation",
        "usage_correction",
        "register",
        "precision",
        "rewrite",
    }
)

TASK_SKILLS: Dict[str, str] = {
    "vn_meaning_mcq": "meaning",
    "meaning_mcq": "meaning",
    "simple_cloze": "context",
    "meaning_in_context": "context",
    "sentence_reorder": "pattern",
    "translate_with_hints": "translation",
    "collocation_mcq": "collocation",
    "pattern_cloze": "pattern",
    "error_diagnosis": "usage_correction",
    "error_correction": "usage_correction",
    "guided_rewrite": "rewrite",
    "targeted_rewrite_challenge": "rewrite",
    "nuance_in_context": "precision",
    "academic_collocation": "collocation",
    "register_choice": "register",
    "rewrite_with_target": "rewrite",
    "precision_nuance_challenge": "precision",
    "precision_in_context": "precision",
    "register_tone_judgment": "register",
    "precision_cloze": "precision",
    "advanced_paraphrase": "rewrite",
    "nuance_rationale_challenge": "precision",
}

SKILL_LABELS: Dict[str, str] = {
    "meaning": "word meaning",
    "context": "meaning in context",
    "collocation": "natural collocations",
    "pattern": "sentence patterns",
    "translation": "translation",
    "usage_correction": "usage correction",
    "register": "register and tone",
    "precision": "precision and nuance",
    "rewrite": "sentence rewrite",
}

PHASES = ("warmup", "focus", "stretch")
TIME_COST_SECONDS = {
    "mcq": 45,
    "reorder": 60,
    "correction": 90,
    "rewrite": 150,
    "free_text": 150,
}
INTENSITY_MINUTES = {"recovery": 6, "standard": 10, "depth": 14}
MAX_REQUIRED_ITEMS = 12
MAX_MICRO_REPAIRS = 2


def canonical_skill(task_type: str, raw_skill: Optional[str] = None) -> str:
    task = str(task_type or "").strip().lower()
    if task in TASK_SKILLS:
        return TASK_SKILLS[task]
    skill = str(raw_skill or "").strip().lower()
    aliases = {
        "usage": "usage_correction",
        "correction": "usage_correction",
        "production": "rewrite",
        "nuance": "precision",
    }
    skill = aliases.get(skill, skill)
    return skill if skill in CANONICAL_SKILLS else "meaning"


def canonical_weakness_dimension(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in CANONICAL_SKILLS:
        return raw
    if raw.endswith("_wrong"):
        raw = raw[: -len("_wrong")]
    aliases = {
        "recall_failed": "meaning",
        "meaning_mcq": "meaning",
        "stale_review_due": "meaning",
        "translation_failed": "translation",
        "topic_sentence_failed": "rewrite",
        "missing_target_word": "rewrite",
        "invalid_language": "rewrite",
        "collocation_weak": "collocation",
        "low_mastery_band": "meaning",
    }
    return aliases.get(raw, canonical_skill(raw))


def mastery_slot_credit_key(
    *,
    track_id: Optional[str],
    mastery_slot: Optional[int],
    fallback_question_id: Optional[str] = None,
    task_type: Optional[str] = None,
) -> str:
    track = str(track_id or "").strip()
    slot = int(mastery_slot or 0)
    if track and slot > 0:
        return f"{track}:slot:{slot}"
    return str(fallback_question_id or f"{task_type or 'meaning'}:{slot}")


def _timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _issue_word_id(row: Dict[str, Any]) -> str:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return str(row.get("word_id") or evidence.get("word_id") or "")


def intensity_for(*, learner_rhythm: str, due_today: int, daily_goal: int) -> str:
    rhythm = str(learner_rhythm or "").strip().lower()
    goal = max(1, int(daily_goal or 5))
    if rhythm == "intermittent" or due_today >= max(goal * 2, 10):
        return "recovery"
    if rhythm == "consistent" and due_today <= max(2, goal // 2):
        return "depth"
    return "standard"


def choose_focus_skill(
    *,
    weaknesses: Iterable[Dict[str, Any]],
    skill_states: Iterable[Dict[str, Any]] = (),
    due_today: int = 0,
) -> str:
    ranked = sorted(
        weaknesses,
        key=lambda row: (
            -float(row.get("severity") or 0),
            -_timestamp(row.get("last_seen_at")),
            str(row.get("due_at") or "9999-12-31"),
            _issue_word_id(row),
            str(row.get("dimension") or ""),
        ),
    )
    if ranked:
        return canonical_weakness_dimension(str(ranked[0].get("dimension") or ""))
    due_states = sorted(
        skill_states,
        key=lambda row: (
            str(row.get("due_at") or ""),
            float(row.get("score") or 0),
            str(row.get("skill_id") or ""),
        ),
    )
    if due_states and due_today > 0:
        return canonical_weakness_dimension(
            str(due_states[0].get("skill_id") or "meaning")
        )
    return "meaning"


def _interaction_cost(row: Dict[str, Any]) -> int:
    interaction = str(row.get("interaction_kind") or "mcq").strip().lower()
    task_type = str(row.get("task_type") or "").strip().lower()
    if "correction" in task_type or "diagnosis" in task_type:
        return TIME_COST_SECONDS["correction"]
    return TIME_COST_SECONDS.get(interaction, TIME_COST_SECONDS["mcq"])


def _sort_key(row: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if row.get("is_due") else 1,
        str(row.get("due_at") or ""),
        int(row.get("mastery_slot") or 1),
        str(row.get("word_id") or ""),
        str(row.get("question_id") or ""),
    )


def _phase_pool(
    candidates: Iterable[Dict[str, Any]], *, phase: str, focus_skill: str
) -> List[Dict[str, Any]]:
    rows = list(candidates)
    if phase == "warmup":
        preferred = [
            row
            for row in rows
            if int(row.get("mastery_slot") or 1) <= 2
            and canonical_skill(str(row.get("task_type") or ""), row.get("skill"))
            in {"meaning", "context"}
        ]
    elif phase == "focus":
        preferred = [
            row
            for row in rows
            if canonical_skill(str(row.get("task_type") or ""), row.get("skill"))
            == focus_skill
        ]
    else:
        preferred = [
            row
            for row in rows
            if int(row.get("mastery_slot") or 1) >= 4
            and canonical_skill(str(row.get("task_type") or ""), row.get("skill"))
            in {
                focus_skill,
                "rewrite",
                "translation",
                "usage_correction",
                "register",
                "precision",
            }
        ]
    return sorted(preferred or rows, key=_sort_key)


def compose_workout_items(
    *,
    candidates: Iterable[Dict[str, Any]],
    focus_skill: str,
    intensity: str,
) -> List[Dict[str, Any]]:
    """Build ordered three-phase workout items from quality-gated candidates."""
    rows = [dict(row) for row in candidates]
    if not rows:
        return []
    focus = canonical_weakness_dimension(focus_skill)
    minutes = INTENSITY_MINUTES.get(intensity, INTENSITY_MINUTES["standard"])
    budget = minutes * 60
    phase_targets = {
        "warmup": max(1, budget * 25 // 100),
        "focus": max(1, budget * 50 // 100),
        "stretch": max(1, budget * 25 // 100),
    }
    selected: List[Dict[str, Any]] = []
    used_questions: set[str] = set()
    used_words_by_phase: Dict[str, set[str]] = defaultdict(set)

    for phase in PHASES:
        spent = 0
        for row in _phase_pool(rows, phase=phase, focus_skill=focus):
            question_id = str(row.get("question_id") or "")
            word_id = str(row.get("word_id") or "")
            if not question_id or not word_id or question_id in used_questions:
                continue
            if word_id in used_words_by_phase[phase]:
                continue
            cost = _interaction_cost(row)
            if selected and spent >= phase_targets[phase]:
                break
            item = {
                **row,
                "phase": phase,
                "skill_id": canonical_skill(
                    str(row.get("task_type") or ""), row.get("skill")
                ),
                "estimated_seconds": cost,
                "is_required": True,
            }
            selected.append(item)
            used_questions.add(question_id)
            used_words_by_phase[phase].add(word_id)
            spent += cost
            if len(selected) >= MAX_REQUIRED_ITEMS:
                return selected
    return selected


def select_micro_repair(
    *,
    candidates: Iterable[Dict[str, Any]],
    failed_item: Dict[str, Any],
    existing_repairs: int,
) -> Optional[Dict[str, Any]]:
    if existing_repairs >= MAX_MICRO_REPAIRS:
        return None
    failed_question = str(failed_item.get("question_id") or "")
    failed_skill = canonical_weakness_dimension(str(failed_item.get("skill_id") or ""))
    pool = sorted(candidates, key=_sort_key)
    for row in pool:
        if str(row.get("question_id") or "") == failed_question:
            continue
        skill = canonical_skill(str(row.get("task_type") or ""), row.get("skill"))
        if skill != failed_skill:
            continue
        return {
            **row,
            "phase": "focus",
            "skill_id": skill,
            "estimated_seconds": _interaction_cost(row),
            "is_required": True,
        }
    return None


def workout_copy(*, focus_skill: str, intensity: str, due_today: int) -> Dict[str, str]:
    label = SKILL_LABELS.get(focus_skill, focus_skill.replace("_", " "))
    minutes = INTENSITY_MINUTES.get(intensity, 10)
    reason = (
        f"You have {due_today} due words. Start with a quick review, then sharpen {label}."
        if due_today > 0
        else f"Build a stronger active vocabulary by sharpening {label}."
    )
    return {
        "eyebrow": "Today's Coaching Workout",
        "headline": f"Focus on {label}",
        "reason": reason,
        "expected_gain": f"A focused {minutes}-minute workout with one clear skill target.",
    }
