"""Assemble daily missions as one word per task from LLM output + template catalog."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from aiforen.domain.vocab_task_templates import (
    ALLOWED_WORD_SOURCES,
    VocabTaskTemplate,
    build_template_catalog_for_context,
    compute_word_count_range,
    default_template_for_mission_type,
    get_vocab_task_template,
)


def _clamp_word_count(value: Any, *, min_count: int, max_count: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = min_count
    return max(min_count, min(max_count, n))


def _clean_word_task_node(node: Dict[str, Any]) -> Dict[str, Any]:
    source = str(node.get("source") or "pool").strip().lower()
    if source not in ALLOWED_WORD_SOURCES:
        source = "pool"
    try:
        priority = int(node.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    word_id = str(node.get("word_id") or "").strip()
    lemma_hint = str(node.get("lemma_hint") or "").strip()[:64]
    note = str(node.get("note") or "").strip()[:120]
    return {
        "word_id": word_id,
        "lemma_hint": lemma_hint,
        "source": source,
        "priority": priority,
        "note": note,
    }


def normalize_vocab_daily_word_mission_payload(
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate LLM word-mission JSON; does not assign word_ids from pool."""
    word_range = compute_word_count_range(context)
    mission_signals = context.get("mission_signals") or {}
    mission_type = str(mission_signals.get("primary_mission_type") or "study_pack")

    template_id = str(payload.get("session_template_id") or "").strip()
    template = get_vocab_task_template(template_id)
    if template is None:
        template = default_template_for_mission_type(mission_type)
        template_id = template.id

    target_count = _clamp_word_count(
        payload.get("target_word_count"),
        min_count=word_range["min"],
        max_count=word_range["max"],
    )

    raw_tasks = payload.get("word_tasks") or []
    word_tasks = [
        _clean_word_task_node(item) for item in raw_tasks if isinstance(item, dict)
    ]
    word_tasks.sort(key=lambda t: (t.get("priority") or 0, t.get("lemma_hint") or ""))
    word_tasks = word_tasks[:target_count]

    raw_coach = payload.get("coach_overview_lines")
    coach_lines: List[str] = []
    if isinstance(raw_coach, list):
        for line in raw_coach[:3]:
            text = str(line).strip()
            if text:
                coach_lines.append(text[:95])

    confidence = payload.get("confidence", 0.7)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.7

    return {
        "coach_overview_lines": coach_lines,
        "session_template_id": template_id,
        "selection_rationale": str(payload.get("selection_rationale") or "")[:500],
        "target_word_count": target_count,
        "word_tasks": word_tasks,
        "confidence": max(0.0, min(1.0, confidence)),
        "headline": "",
        "summary": "",
        "primary_cta": {},
    }


def expand_word_tasks_to_plan_blocks(
    *,
    word_tasks: List[Dict[str, Any]],
    template: VocabTaskTemplate,
    pack_id: Optional[str],
    locale: str,
) -> List[Dict[str, Any]]:
    """One plan_block per resolved word (target_count=1)."""
    vi = str(locale).lower().startswith("vi")
    blocks: List[Dict[str, Any]] = []
    for index, task in enumerate(word_tasks):
        word_id = str(task.get("word_id") or "").strip()
        if not word_id:
            continue
        lemma = str(task.get("lemma_hint") or "").strip() or word_id[:8]
        title = (
            f"{template.label_vi}: {lemma}" if vi else f"{template.label_en}: {lemma}"
        )
        desc = str(task.get("note") or template.description_for_llm)[:120]
        blocks.append(
            {
                "task_id": f"wt-{word_id}",
                "type": template.block_type,
                "title": title[:48],
                "description": desc,
                "target_count": 1,
                "pack_id": pack_id,
                "word_ids": [word_id],
                "task_steps": list(template.task_steps),
                "word_task_source": task.get("source"),
                "session_template_id": template.id,
                "plan_block_index": index,
            }
        )
    return blocks


async def assign_word_tasks(
    *,
    llm_payload: Dict[str, Any],
    context: Dict[str, Any],
    template: VocabTaskTemplate,
    pack_id: Optional[str],
    fetch_word_ids: Callable[..., Any],
    fetch_wrong_word_ids: Callable[[int], List[str]],
) -> List[Dict[str, Any]]:
    """
      Resolve word_ids for each word_task; fill from pool up to target_word_count.

      fetch_word_ids(pack_id, target, mode, exclude) -> list[str]
    fetch_wrong_word_ids(target) -> list[str] from recent_actions
    """
    target = int(llm_payload.get("target_word_count") or 5)
    reserved: Set[str] = set()
    resolved: List[Dict[str, Any]] = []

    def add_word(
        word_id: str, source: str, lemma_hint: str = "", note: str = ""
    ) -> bool:
        cleaned = str(word_id).strip()
        if not cleaned or cleaned in reserved:
            return False
        reserved.add(cleaned)
        resolved.append(
            {
                "word_id": cleaned,
                "lemma_hint": lemma_hint,
                "source": source,
                "priority": len(resolved),
                "note": note,
            }
        )
        return True

    for task in llm_payload.get("word_tasks") or []:
        if len(resolved) >= target:
            break
        if not isinstance(task, dict):
            continue
        wid = str(task.get("word_id") or "").strip()
        if wid:
            add_word(
                wid,
                str(task.get("source") or "pool"),
                str(task.get("lemma_hint") or ""),
                str(task.get("note") or ""),
            )

    wrong_pool: List[str] = []
    if "wrong_answer" in template.prefers_sources:
        wrong_pool = await fetch_wrong_word_ids(target)

    for wid in wrong_pool:
        if len(resolved) >= target:
            break
        add_word(wid, "wrong_answer")

    mode = "review_due" if template.block_type == "review_due" else "study_pack"
    source_order = list(template.prefers_sources)

    async def pull_from_pack(src: str, count: int) -> None:
        if not pack_id or count <= 0:
            return
        pool_mode = "review_due" if src == "due" else mode
        ids = await fetch_word_ids(
            pack_id=str(pack_id),
            target=count,
            mode=pool_mode,
            exclude=reserved,
        )
        for wid in ids:
            if len(resolved) >= target:
                break
            add_word(wid, src if src in ALLOWED_WORD_SOURCES else "pool")

    remaining = target - len(resolved)
    for src in source_order:
        if remaining <= 0:
            break
        if src == "wrong_answer":
            continue
        await pull_from_pack(src, remaining)
        remaining = target - len(resolved)

    if remaining > 0 and pack_id:
        await pull_from_pack("pool", remaining)

    return resolved[:target]


def build_word_mission_context_extras(context: Dict[str, Any]) -> Dict[str, Any]:
    """Fields injected before LLM call."""
    return {
        "task_template_catalog": build_template_catalog_for_context(context),
        "word_count_range": compute_word_count_range(context),
        "word_task_mission": True,
    }


def session_template_payload(
    template: VocabTaskTemplate, locale: str
) -> Dict[str, Any]:
    vi = str(locale).lower().startswith("vi")
    return {
        "id": template.id,
        "label": template.label_vi if vi else template.label_en,
        "task_steps": list(template.task_steps),
        "session_mode": template.session_mode,
        "block_type": template.block_type,
        "quiz_focus": template.quiz_focus,
    }


def build_rules_word_mission_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback when LLM fails — rules-only template + empty word_tasks for assign."""
    mission_signals = context.get("mission_signals") or {}
    mission_type = str(mission_signals.get("primary_mission_type") or "study_pack")
    template = default_template_for_mission_type(mission_type)
    word_range = compute_word_count_range(context)
    target = word_range["min"]
    stats = context.get("stats") or {}
    due = int(stats.get("due_today") or 0)
    if due > 0 and template.block_type == "review_due":
        target = min(word_range["max"], max(word_range["min"], due))

    return normalize_vocab_daily_word_mission_payload(
        {
            "session_template_id": template.id,
            "selection_rationale": "Rules fallback: template from mission_signals.",
            "target_word_count": target,
            "word_tasks": [],
            "coach_overview_lines": [],
            "confidence": 0.55,
        },
        context=context,
    )


def new_task_id() -> str:
    return f"wt-{uuid.uuid4().hex[:12]}"
