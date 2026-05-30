"""Rank vocab daily mission: mistake patterns beat review hygiene unless backlog is severe."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

MissionType = Literal["repair_weakness", "review_recall", "study_pack"]
ReviewStatus = Literal["clear", "light", "medium", "heavy"]

HYGIENE_DIMENSIONS = frozenset({"stale_review_due", "review_due"})


def review_status(overdue_count: int) -> ReviewStatus:
    if overdue_count <= 0:
        return "clear"
    if overdue_count <= 10:
        return "light"
    if overdue_count <= 30:
        return "medium"
    return "heavy"


def is_hygiene_weakness(weakness: Dict[str, Any]) -> bool:
    return str(weakness.get("dimension") or "").strip().lower() in HYGIENE_DIMENSIONS


def mistake_weakness_score(weakness: Dict[str, Any]) -> float:
    if is_hygiene_weakness(weakness):
        return 0.0

    evidence = int(weakness.get("evidence_count") or 0)
    severity = float(weakness.get("severity") or 0)
    evidence_meta = weakness.get("evidence") or {}
    recent_wrong = int(evidence_meta.get("recent_wrong_count") or 0)
    low_accuracy_penalty = max(0.0, 1.0 - min(severity / 5.0, 1.0)) * 2.0

    return evidence * 3 + recent_wrong * 4 + low_accuracy_penalty + evidence


def review_priority_score(
    overdue_count: int, *, very_old_overdue_count: int = 0
) -> float:
    return overdue_count * 1 + very_old_overdue_count * 2


def pick_primary_weakness(
    weaknesses: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    actionable = [w for w in weaknesses if not is_hygiene_weakness(w)]
    if not actionable:
        return None
    return max(actionable, key=mistake_weakness_score)


def pick_primary_mission_type(
    *,
    weaknesses: List[Dict[str, Any]],
    due_today: int,
    very_old_overdue_count: int = 0,
) -> Tuple[MissionType, Optional[Dict[str, Any]]]:
    primary_weakness = pick_primary_weakness(weaknesses)
    top_evidence = int((primary_weakness or {}).get("evidence_count") or 0)
    status = review_status(due_today)

    if status == "heavy" and top_evidence < 2:
        return "review_recall", primary_weakness

    if primary_weakness and top_evidence >= 1:
        return "repair_weakness", primary_weakness

    weakness_score = (
        mistake_weakness_score(primary_weakness) if primary_weakness else 0.0
    )
    review_score = review_priority_score(
        due_today, very_old_overdue_count=very_old_overdue_count
    )

    if primary_weakness and weakness_score >= review_score * 0.7:
        return "repair_weakness", primary_weakness

    if due_today > 0:
        return "review_recall", primary_weakness

    if primary_weakness:
        return "repair_weakness", primary_weakness

    return "study_pack", None


def rank_mission_weaknesses(weaknesses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actionable = [w for w in weaknesses if not is_hygiene_weakness(w)]
    hygiene = [w for w in weaknesses if is_hygiene_weakness(w)]
    actionable.sort(key=mistake_weakness_score, reverse=True)
    hygiene.sort(key=lambda row: int(row.get("evidence_count") or 0), reverse=True)
    return actionable + hygiene


def build_mission_signals(
    *,
    weaknesses: List[Dict[str, Any]],
    due_today: int,
    very_old_overdue_count: int = 0,
) -> Dict[str, Any]:
    mission_type, primary_weakness = pick_primary_mission_type(
        weaknesses=weaknesses,
        due_today=due_today,
        very_old_overdue_count=very_old_overdue_count,
    )
    return {
        "primary_mission_type": mission_type,
        "review_status": review_status(due_today),
        "due_today": due_today,
        "review_priority_score": review_priority_score(
            due_today,
            very_old_overdue_count=very_old_overdue_count,
        ),
        "primary_weakness_dimension": (primary_weakness or {}).get("dimension"),
        "primary_weakness_label": (primary_weakness or {}).get("label"),
        "primary_weakness_evidence": int(
            (primary_weakness or {}).get("evidence_count") or 0
        ),
    }


_BLOCK_ORDER: Dict[MissionType, List[str]] = {
    "repair_weakness": [
        "repair_weakness",
        "review_due",
        "production_practice",
        "study_pack",
    ],
    "review_recall": [
        "review_due",
        "repair_weakness",
        "production_practice",
        "study_pack",
    ],
    "study_pack": [
        "study_pack",
        "repair_weakness",
        "review_due",
        "production_practice",
    ],
}


def reorder_plan_blocks(
    blocks: List[Dict[str, Any]],
    *,
    mission_type: MissionType,
) -> List[Dict[str, Any]]:
    if not blocks:
        return blocks

    order = _BLOCK_ORDER.get(mission_type, _BLOCK_ORDER["repair_weakness"])
    rank = {block_type: index for index, block_type in enumerate(order)}

    def _key(block: Dict[str, Any]) -> tuple[int, int]:
        block_type = str(block.get("type") or "study_pack")
        return (rank.get(block_type, len(order)), 0)

    return sorted(blocks, key=_key)
