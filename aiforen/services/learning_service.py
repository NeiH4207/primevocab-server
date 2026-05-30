"""Learning service: grammar + vocab content, progress, stats, sessions."""

# flake8: noqa: E402

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _as_utc_aware(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


from aiforen.domain.vocab_calibration_cefr import (
    cefr_to_ielts_mid,
    estimate_cefr_from_answers,
    pick_recommended_pack_id,
)
from aiforen.domain.vocab_daily_streak import (
    compute_daily_streak,
    vocab_mistakes_from_daily_activity,
)
from aiforen.domain.vocab_learner_rhythm import (
    build_coach_overview_lines,
    classify_learner_rhythm,
    classify_learner_stage,
    normalize_coach_overview_lines,
)
from aiforen.domain.vocab_mission_priority import (
    build_mission_signals,
    rank_mission_weaknesses,
    reorder_plan_blocks,
)

_MISSION_WEAKNESS_REPAIR_VI: Dict[str, str] = {
    "stale_review_due": "Review các từ cần ôn để giữ recall ổn định.",
    "meaning_mcq_wrong": "Bạn đang nhầm nghĩa khi chọn đáp án. Hãy làm lại MCQ với các từ sai trước khi học thêm.",
    "translation_failed": "Luyện sentence practice — viết câu có dùng từ mục tiêu.",
    "topic_sentence_failed": "Luyện sentence practice — viết câu ngắn, đúng ngữ cảnh và có target word.",
    "low_mastery_band": "Tập trung pack band còn yếu trước khi học quá nhiều từ mới.",
    "recall_failed": "Ôn lại nghĩa và tự recall từ trước khi chuyển sang bài mới.",
    "collocation_weak": "Luyện collocation bằng câu ngắn để dùng từ tự nhiên hơn.",
}

_MISSION_WEAKNESS_LABEL: Dict[str, str] = {
    "stale_review_due": "từ cần review",
    "review_due": "từ cần review",
    "meaning_mcq_wrong": "Meaning MCQ",
    "translation_failed": "Sentence practice",
    "topic_sentence_failed": "Sentence practice",
    "low_mastery_band": "low mastery band",
    "recall_failed": "word recall",
    "collocation_weak": "collocation",
    "weak_stat_label": "vocabulary group",
}

_REVIEW_DIMENSIONS = {"stale_review_due", "review_due"}

_CALIBRATION_PACK_PLAN: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_oxford_a1",
        "count": 3,
        "label": "A1",
        "difficulty": "foundation",
    },
    {
        "pack_id": "pack_oxford_a2",
        "count": 3,
        "label": "A2",
        "difficulty": "basic",
    },
    {
        "pack_id": "pack_oxford_b1",
        "count": 3,
        "label": "B1",
        "difficulty": "intermediate",
    },
    {
        "pack_id": "pack_oxford_b2",
        "count": 4,
        "label": "B2",
        "difficulty": "upper-intermediate",
    },
    {
        "pack_id": "pack_oxford_c1",
        "count": 4,
        "label": "C1",
        "difficulty": "advanced",
    },
    {
        "pack_id": "pack_gre",
        "count": 3,
        "label": "C2",
        "difficulty": "extreme",
    },
]

_CALIBRATION_PACK_FALLBACKS: List[Dict[str, Any]] = [
    {"pack_id": "pack_band_7", "count": 4, "label": "Band 7", "difficulty": "hard"},
    {
        "pack_id": "pack_band_8",
        "count": 4,
        "label": "Band 8",
        "difficulty": "very hard",
    },
    {"pack_id": "pack_band_6", "count": 3, "label": "Band 6", "difficulty": "medium"},
]


def _localize_mission_weaknesses(
    weaknesses: List[Dict[str, Any]],
    locale: str,
) -> List[Dict[str, Any]]:
    if not str(locale).lower().startswith("vi"):
        out: List[Dict[str, Any]] = []
        for weakness in weaknesses:
            row = dict(weakness)
            dim = str(row.get("dimension") or "")
            if dim in _MISSION_WEAKNESS_LABEL:
                row["label"] = _MISSION_WEAKNESS_LABEL[dim]
            out.append(row)
        return out
    out: List[Dict[str, Any]] = []
    for weakness in weaknesses:
        row = dict(weakness)
        dim = str(row.get("dimension") or "")
        if dim in _MISSION_WEAKNESS_LABEL:
            row["label"] = _MISSION_WEAKNESS_LABEL[dim]
        if dim in _MISSION_WEAKNESS_REPAIR_VI:
            row["suggested_repair"] = _MISSION_WEAKNESS_REPAIR_VI[dim]
        out.append(row)
    return out


def _weakness_display_label(weakness: Optional[Dict[str, Any]]) -> str:
    if not weakness:
        return "weak spot"
    dim = str(weakness.get("dimension") or "").strip().lower()
    if dim in _MISSION_WEAKNESS_LABEL:
        return _MISSION_WEAKNESS_LABEL[dim]
    return str(weakness.get("label") or "weak spot").strip() or "weak spot"


def _is_review_weakness(weakness: Optional[Dict[str, Any]]) -> bool:
    dim = str((weakness or {}).get("dimension") or "").strip().lower()
    return dim in _REVIEW_DIMENSIONS


def _mission_evidence_count(weakness: Optional[Dict[str, Any]]) -> int:
    try:
        return int((weakness or {}).get("evidence_count") or 0)
    except (TypeError, ValueError):
        return 0


def _pick_mission_type_for_display(
    *,
    weaknesses: List[Dict[str, Any]],
    due_today: int,
) -> tuple[str, Optional[Dict[str, Any]]]:
    """Prefer actionable mistake patterns over review backlog.

    Review backlog should support the session, not dominate the mission,
    unless there is no mistake pattern to repair.
    """
    actionable = [w for w in weaknesses if not _is_review_weakness(w)]
    if actionable:
        ranked = rank_mission_weaknesses(actionable)
        return "repair_weakness", ranked[0]

    review = next((w for w in weaknesses if _is_review_weakness(w)), None)
    if due_today > 0 or review:
        return "review_recall", review

    return "study_pack", None


def _mission_micro_goal(mastery_pct: Any) -> Optional[float]:
    try:
        mastery = float(mastery_pct or 0)
    except (TypeError, ValueError):
        return None
    if mastery >= 100:
        return 100.0
    return min(100.0, max(float(int(mastery) + 1), round(mastery + 0.4, 1)))


def _estimate_mission_minutes(
    *,
    mission_type: str,
    due_today: int,
    daily_goal: int,
    plan_blocks: List[Dict[str, Any]],
) -> int:
    review_minutes = min(due_today, max(0, daily_goal)) * 1
    block_minutes = 3 * max(1, len(plan_blocks))
    if mission_type == "repair_weakness":
        return min(16, max(8, review_minutes + block_minutes))
    if mission_type == "review_recall":
        return min(18, max(7, review_minutes + 2))
    return min(14, max(7, block_minutes + daily_goal))


def _build_mission_copy(
    *,
    mission_type: str,
    primary_weakness: Optional[Dict[str, Any]],
    due_today: int,
    locale: str,
    focus_band_label: Optional[str] = None,
    focus_band_mastery_pct: Any = None,
) -> tuple[str, str, str, str, str]:
    vi = str(locale).lower().startswith("vi")
    weak_label = _weakness_display_label(primary_weakness)
    evidence_count = _mission_evidence_count(primary_weakness)
    evidence_word = "lỗi" if evidence_count < 3 else "weak spot"
    next_goal = _mission_micro_goal(focus_band_mastery_pct)

    if mission_type == "repair_weakness" and primary_weakness:
        headline = (
            f"Hôm nay: sửa lỗi {weak_label}" if vi else f"Today: fix {weak_label}"
        )
        if due_today > 0:
            summary = (
                f"Bạn có {evidence_count} {evidence_word} {weak_label}. "
                f"{due_today} từ cũng cần review — mình đưa vào session sửa lỗi."
                if vi
                else (
                    f"Recent {weak_label} mistakes detected. {due_today} review words "
                    "are included in this repair session."
                )
            )
        else:
            summary = (
                f"Bạn có {evidence_count} {evidence_word} {weak_label}. "
                "Session này sẽ repair, review từ liên quan, rồi dùng từ trong câu."
                if vi
                else (
                    f"Recent {weak_label} mistakes detected. Repair, review related words, "
                    "then practice in sentences."
                )
            )
        reason = (
            f"{weak_label} là pattern lỗi rõ nhất gần đây. Sửa lỗi trước giúp recall ổn hơn khi học từ mới."
            if vi
            else f"{weak_label} is the clearest recent mistake pattern. Repair it before adding more words."
        )
        expected_gain = (
            (
                f"Goal: kéo {focus_band_label} mastery lên gần {next_goal:g}%."
                if focus_band_label and next_goal is not None
                else "Goal: giảm lỗi lặp lại trong session hôm nay."
            )
            if vi
            else (
                f"Goal: move {focus_band_label} mastery toward {next_goal:g}%."
                if focus_band_label and next_goal is not None
                else "Goal: reduce repeat mistakes today."
            )
        )
        cta_label = (
            (
                "Sửa lỗi MCQ"
                if "mcq" in weak_label.lower()
                else "Luyện sentence" if "sentence" in weak_label.lower() else "Sửa lỗi"
            )
            if vi
            else "Repair mistake"
        )
        return (
            headline[:45],
            summary[:160],
            cta_label[:24],
            reason[:220],
            expected_gain[:140],
        )

    if mission_type == "review_recall" and due_today > 0:
        headline = (
            f"Hôm nay: review {due_today} từ cần ôn"
            if vi
            else f"Today: review {due_today} words"
        )
        summary = (
            f"Bạn có {due_today} từ cần review. Làm nhanh để bảo vệ recall, rồi quay lại học từ mới."
            if vi
            else f"You have {due_today} words to review. Clear them first to protect recall."
        )
        reason = (
            "Không có pattern lỗi đủ rõ, nên review queue là việc tốt nhất để giữ recall hôm nay."
            if vi
            else "No reliable mistake pattern was found, so review is the best action today."
        )
        expected_gain = (
            "Goal: giảm backlog review và giữ mastery ổn định."
            if vi
            else "Goal: reduce review backlog and keep mastery stable."
        )
        return (
            headline[:45],
            summary[:160],
            "Review từ cần ôn"[:24],
            reason[:220],
            expected_gain[:140],
        )

    band_label = focus_band_label or ("vocab momentum" if not vi else "vocab momentum")
    headline = f"Hôm nay: build {band_label}" if vi else f"Today: build {band_label}"
    summary = (
        "Review queue đang ổn. Học một batch nhỏ để build momentum."
        if vi
        else "Your review queue is stable. Learn a small batch to build momentum."
    )
    reason = (
        "Khi không có lỗi nổi bật, một batch nhỏ giúp duy trì streak mà không overload recall."
        if vi
        else "When there is no clear mistake pattern, a small batch keeps momentum without overloading recall."
    )
    expected_gain = (
        (
            f"Goal: kéo {focus_band_label} mastery lên gần {next_goal:g}%."
            if focus_band_label and next_goal is not None
            else "Goal: hoàn thành một focused session."
        )
        if vi
        else (
            f"Goal: move {focus_band_label} mastery toward {next_goal:g}%."
            if focus_band_label and next_goal is not None
            else "Goal: complete one focused session."
        )
    )
    return (
        headline[:45],
        summary[:160],
        "Học từ mới"[:24],
        reason[:220],
        expected_gain[:140],
    )


PLAN_BLOCK_WORD_TARGET_MIN = 5
PLAN_BLOCK_WORD_TARGET_MAX = 8
PLAN_BLOCK_SENTENCE_TARGET_MIN = 3
PLAN_BLOCK_SENTENCE_TARGET_MAX = 5


def _clamp_plan_block_target(
    block_type: str,
    raw: int,
    *,
    due_today: int = 0,
    daily_goal: int = 5,
) -> int:
    if block_type == "production_practice":
        base = raw or PLAN_BLOCK_SENTENCE_TARGET_MIN
        return min(
            max(base, PLAN_BLOCK_SENTENCE_TARGET_MIN),
            PLAN_BLOCK_SENTENCE_TARGET_MAX,
        )
    if block_type == "review_due":
        if due_today > 0:
            return min(max(due_today, 1), PLAN_BLOCK_WORD_TARGET_MAX)
        base = raw or min(daily_goal, PLAN_BLOCK_WORD_TARGET_MAX)
        return min(max(base, PLAN_BLOCK_WORD_TARGET_MIN), PLAN_BLOCK_WORD_TARGET_MAX)
    base = raw or min(
        max(daily_goal, PLAN_BLOCK_WORD_TARGET_MIN), PLAN_BLOCK_WORD_TARGET_MAX
    )
    return min(max(base, PLAN_BLOCK_WORD_TARGET_MIN), PLAN_BLOCK_WORD_TARGET_MAX)


def _rewrite_plan_block_count_phrases(
    text: Any,
    target_count: int,
    *,
    word_mode: bool,
) -> str:
    value = str(text or "")
    if not value or target_count <= 0:
        return value
    if word_mode:
        unit_tokens = r"(?:từ|word|words)"
        replacement = f"{target_count} từ"
    else:
        unit_tokens = r"(?:câu|sentence|sentences)"
        replacement = f"{target_count} câu"
    value = re.sub(
        rf"\b\d{{1,2}}\s*[–—-]\s*\d{{1,2}}\s*{unit_tokens}\b",
        replacement,
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(
        rf"\b\d{{1,2}}\s*{unit_tokens}\b",
        replacement,
        value,
        flags=re.IGNORECASE,
    )


def _normalize_plan_blocks_copy(
    blocks: List[Dict[str, Any]],
    *,
    mission_type: str,
    primary_weakness: Optional[Dict[str, Any]],
    due_today: int,
    daily_goal: int,
    locale: str,
) -> List[Dict[str, Any]]:
    vi = str(locale).lower().startswith("vi")
    weak_label = _weakness_display_label(primary_weakness)
    repair_hint = ""
    if primary_weakness:
        dim = str(primary_weakness.get("dimension") or "").strip().lower()
        repair_hint = (
            primary_weakness.get("suggested_repair")
            or _MISSION_WEAKNESS_REPAIR_VI.get(dim)
            or (
                "Làm lại các câu sai và xem nghĩa đúng."
                if vi
                else "Redo wrong items and check meanings."
            )
        )

    normalized: List[Dict[str, Any]] = []
    for block in blocks:
        row = dict(block)
        block_type = str(row.get("type") or "study_pack")
        title_l = str(row.get("title") or "").lower()
        desc_l = str(row.get("description") or "").lower()
        if block_type == "study_pack" and (
            "luyện câu" in title_l
            or "sentence practice" in title_l
            or ("câu ngắn" in desc_l and "từ" not in desc_l[:40])
        ):
            row["type"] = "production_practice"
            block_type = "production_practice"
        if block_type == "repair_weakness" and primary_weakness:
            row["title"] = f"Sửa lỗi {weak_label}" if vi else f"Repair {weak_label}"
            row["description"] = repair_hint[:120]
        elif block_type == "review_due" and due_today > 0:
            count = min(due_today, PLAN_BLOCK_WORD_TARGET_MAX)
            row["target_count"] = count
            if mission_type == "repair_weakness":
                row["title"] = "Review từ liên quan" if vi else "Review related words"
                row["description"] = (
                    f"Review {count} từ cần ôn có liên quan trong session sửa lỗi."
                    if vi
                    else f"Review {count} related review words in this repair session."
                )[:120]
            else:
                row["title"] = "Review từ cần ôn" if vi else "Review words"
                row["description"] = (
                    f"Review {count} từ cần ôn trước khi học thêm."
                    if vi
                    else f"Review {count} words before learning new ones."
                )[:120]
        elif block_type == "production_practice":
            row["title"] = "Sentence practice" if vi else "Sentence practice"
            row["description"] = (
                "Viết 3 câu ngắn với các từ vừa sửa."
                if vi
                else "Write 3 short sentences with the words you just repaired."
            )[:120]
            row["target_count"] = PLAN_BLOCK_SENTENCE_TARGET_MIN
        word_mode = block_type != "production_practice"
        target_count = _clamp_plan_block_target(
            block_type,
            int(row.get("target_count") or 0),
            due_today=due_today,
            daily_goal=daily_goal,
        )
        row["target_count"] = target_count
        row["title"] = _rewrite_plan_block_count_phrases(
            row.get("title"), target_count, word_mode=word_mode
        )
        row["description"] = _rewrite_plan_block_count_phrases(
            row.get("description"), target_count, word_mode=word_mode
        )
        if not row.get("task_steps"):
            row["task_steps"] = _mission_task_steps_for_block(block_type)
        normalized.append(row)
    return normalized


def _backfill_mission_pack_ids(
    plan_blocks: List[Dict[str, Any]],
    primary_cta: Dict[str, Any],
    *,
    default_pack_id: Optional[str],
) -> None:
    if not default_pack_id:
        return
    resolved = (
        primary_cta.get("pack_id")
        or next((b.get("pack_id") for b in plan_blocks if b.get("pack_id")), None)
        or default_pack_id
    )
    if not resolved:
        return
    if not primary_cta.get("pack_id"):
        primary_cta["pack_id"] = resolved
    for block in plan_blocks:
        if not block.get("pack_id"):
            block["pack_id"] = resolved


def _dedupe_word_ids(word_ids: List[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for word_id in word_ids:
        cleaned = str(word_id).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _mission_task_steps_for_block(block_type: str) -> List[str]:
    if block_type in {"repair_weakness", "review_due"}:
        return ["mcq"]
    if block_type == "production_practice":
        return ["topic"]
    return ["learn", "mcq"]


from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.errors import QuotaExceeded
from aiforen.domain.enums import QuotaKind
from aiforen.domain.vocab_mastery_score import (
    QUIZ_MATRIX_SLOTS,
    apply_calendar_decay,
    delta_learn,
    delta_quiz_slot,
    display_step_for_word,
    migrate_legacy_word_points,
    word_budget_pct,
)
from aiforen.integrations.llm.factory import get_llm_provider
from aiforen.integrations.llm.json_utils import (
    normalize_vocab_calibration_payload,
    normalize_vocab_daily_mission_payload,
)
from aiforen.repositories.pg.grammar import GrammarRepo
from aiforen.repositories.pg.learning_progress import LearningProgressRepo
from aiforen.repositories.pg.personalization import LearningPersonalizationRepo
from aiforen.repositories.pg.user_stats import UserStatsRepo
from aiforen.repositories.pg.vocab_attempts import VocabAttemptRepo
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.repositories.pg.writing import WritingSubmissionRepo
from aiforen.services.quota_service import QuotaService

_VIETNAMESE_CHAR_RE = re.compile(r"[\u00C0-\u024F\u1E00-\u1EFF]")


def _friendly_vocab_ai_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "529" in text or "overloaded" in text:
        return (
            "The AI service is busy right now. Wait a few seconds, then try again — "
            "your sentence is already saved."
        )
    if "429" in text or "rate" in text:
        return "Too many AI requests. Please try again in a minute."
    if "timeout" in text or "timed out" in text:
        return "The AI request timed out. Please try again."
    return "AI feedback is temporarily unavailable. Please try again shortly."


def _looks_like_vietnamese(text: str) -> bool:
    s = text.strip()
    if len(s) < 2:
        return False
    return bool(_VIETNAMESE_CHAR_RE.search(s))


def _vocab_quiz_invalid_language_feedback(*, learner_answer: str) -> Dict[str, Any]:
    return {
        "ai_status": "invalid_language",
        "passed": False,
        "status": "fail",
        "unavailable_message": (
            "Câu trả lời cần viết bằng tiếng Anh và có dùng từ mục tiêu. "
            "Không gõ tiếng Việt vào ô này."
        ),
        "recommendation": (
            "Viết câu tiếng Anh hoàn chỉnh, có dùng từ mục tiêu. "
            "Ví dụ: dịch câu tiếng Việt ở đề sang English."
        ),
        "corrected_sentence": learner_answer,
    }


def _vocab_quiz_ai_unavailable_feedback(exc: Exception) -> Dict[str, Any]:
    return {
        "ai_status": "unavailable",
        "passed": False,
        "status": "fail",
        "unavailable_message": _friendly_vocab_ai_error(exc),
        "recommendation": "",
    }


def _quiz_production_needs_ai(question: Any) -> bool:
    interaction = (getattr(question, "interaction_kind", None) or "mcq").strip().lower()
    return interaction in ("free_text", "rewrite")


class LearningService:
    def __init__(self, pg: AsyncSession):
        self.pg = pg
        self.grammar = GrammarRepo(pg)
        self.vocab_attempts = VocabAttemptRepo(pg)
        self.progress = LearningProgressRepo(pg)
        self.stats = UserStatsRepo(pg)
        self.lexicon = VocabLexiconRepo(pg)
        self.personalization = LearningPersonalizationRepo(pg)
        self.writing_submissions = WritingSubmissionRepo(pg)

    def _quota_service(self) -> QuotaService:
        return QuotaService(self.pg)

    async def vocab_ai_quota_snapshot(
        self, user_id: str, plan_code: str
    ) -> Dict[str, Any]:
        is_paid = plan_code not in ("free", "guest")
        quota = self._quota_service()
        if is_paid:
            used, limit = await quota.snapshot(user_id, QuotaKind.ai_feedback)
            unlimited = limit <= 0
            remaining = None if unlimited else max(0, limit - used)
            return {
                "is_paid": True,
                "used": used,
                "limit": limit,
                "remaining": remaining,
                "exhausted": not unlimited and used >= limit,
                "period": "daily",
            }

        used, limit = await quota.snapshot(user_id, QuotaKind.vocab_ai_eval)
        remaining = max(0, limit - used)
        return {
            "is_paid": False,
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "exhausted": used >= limit,
            "period": "lifetime",
        }

    async def _check_vocab_ai_quota(
        self, user_id: str, plan_code: str
    ) -> tuple[bool, Optional[str]]:
        snap = await self.vocab_ai_quota_snapshot(user_id, plan_code)
        if not snap.get("exhausted"):
            return True, None
        if snap.get("is_paid"):
            return (
                False,
                "You've reached today's AI feedback limit. Try again tomorrow or upgrade your plan.",
            )
        return (
            False,
            "You've used all free AI evaluations. Upgrade to a vocab plan for more.",
        )

    async def _consume_vocab_ai_quota(
        self, user_id: str, plan_code: str
    ) -> tuple[bool, Optional[str]]:
        is_paid = plan_code not in ("free", "guest")
        quota = self._quota_service()
        try:
            if is_paid:
                await quota.consume(user_id, QuotaKind.ai_feedback)
            else:
                await quota.consume(user_id, QuotaKind.vocab_ai_eval)
            return True, None
        except QuotaExceeded:
            if is_paid:
                return (
                    False,
                    "You've reached today's AI feedback limit. Try again tomorrow or upgrade your plan.",
                )
            return (
                False,
                "You've used all free AI evaluations. Upgrade to a vocab plan for more.",
            )

    async def _grade_vocab_quiz_with_ai(
        self,
        *,
        user_id: str,
        plan_code: str,
        question: Any,
        word: Dict[str, Any],
        free_text_answer: str,
    ) -> Tuple[
        bool, Dict[str, Any], Optional[Dict[str, Any]], bool, bool, Optional[str]
    ]:
        """Returns is_correct, answer_meta, ai_feedback, ai_eval_failed, ai_quota_exceeded, upgrade_hint."""

        payload = question.payload if isinstance(question.payload, dict) else {}
        answer_meta: Dict[str, Any] = {
            "interaction_kind": (question.interaction_kind or "free_text")
            .strip()
            .lower(),
            "free_text_answer": free_text_answer.strip(),
            "grading_method": payload.get("grading_method") or "ai_rubric",
        }
        model_answer = str(payload.get("model_answer") or "").strip()
        answer_meta["model_answer"] = model_answer

        allowed, upgrade_hint = await self._check_vocab_ai_quota(user_id, plan_code)
        if not allowed:
            is_correct, _, answer_meta = self._grade_vocab_quiz_answer(
                question,
                selected_option_id=None,
                free_text_answer=free_text_answer,
                reorder_order=None,
            )
            return is_correct, answer_meta, None, False, True, upgrade_hint

        if _looks_like_vietnamese(free_text_answer):
            feedback = _vocab_quiz_invalid_language_feedback(
                learner_answer=free_text_answer
            )
            return False, answer_meta, feedback, False, False, None

        target_word = str(
            payload.get("target_word")
            or payload.get("required_word")
            or word.get("word")
            or ""
        ).strip()
        prompt_text = str(question.prompt or "").strip()
        context_text = str(payload.get("context") or "").strip()
        task_type = str(
            question.type or payload.get("task_type") or "production"
        ).strip()
        rubric = payload.get("ai_grading_rubric")
        if not isinstance(rubric, list):
            rubric = []
        ai_scoring = payload.get("ai_scoring")
        if not isinstance(ai_scoring, dict):
            ai_scoring = None
        flexibility = str(
            payload.get("accepted_flexibility")
            or payload.get("answer_flexibility")
            or ""
        ).strip()

        ai_eval_failed = False
        ai_feedback: Optional[Dict[str, Any]] = None
        try:
            ai_feedback = await get_llm_provider().evaluate_vocab_quiz(
                task_type=task_type,
                prompt=prompt_text,
                context=context_text,
                learner_answer=free_text_answer,
                target_word=target_word,
                model_answer=model_answer,
                source_sentence=str(payload.get("source_sentence") or "").strip(),
                rubric=[str(x) for x in rubric if str(x).strip()],
                accepted_flexibility=flexibility,
                ai_scoring=ai_scoring,
            )
            await self._consume_vocab_ai_quota(user_id, plan_code)
        except Exception as exc:
            ai_eval_failed = True
            logger.error(
                "vocab quiz AI eval failed user={} question={} err_type={} err={}",
                user_id,
                getattr(question, "id", None),
                type(exc).__name__,
                exc,
            )
            ai_feedback = _vocab_quiz_ai_unavailable_feedback(exc)
            is_correct, _, answer_meta = self._grade_vocab_quiz_answer(
                question,
                selected_option_id=None,
                free_text_answer=free_text_answer,
                reorder_order=None,
            )
            answer_meta["ai_fallback_match"] = True
            return is_correct, answer_meta, ai_feedback, ai_eval_failed, False, None

        is_correct = bool(ai_feedback.get("passed"))
        answer_meta["ai_score"] = ai_feedback.get("score")
        answer_meta["ai_pass_score"] = ai_feedback.get("pass_score")
        return is_correct, answer_meta, ai_feedback, False, False, None

    async def _record_vocab_personalization(
        self,
        *,
        user_id: str,
        event_type: str,
        word_id: Optional[str],
        word: Optional[Dict[str, Any]],
        progress: Optional[Dict[str, Any]],
        pack_mastery_pct: Optional[float],
        question_type: Optional[str] = None,
        step: Optional[str] = None,
        is_correct: Optional[bool] = None,
        score: Optional[float] = None,
        time_taken: int = 0,
        answer_meta: Optional[Dict[str, Any]] = None,
        ai_eval_meta: Optional[Dict[str, Any]] = None,
        weakness_tags: Optional[List[str]] = None,
        occurred_at: Optional[datetime] = None,
    ) -> None:
        try:
            await self.personalization.record_vocab_event(
                user_id=user_id,
                event_type=event_type,
                word_id=word_id,
                pack_id=(word or {}).get("pack_id") or (progress or {}).get("pack_id"),
                question_type=question_type or (word or {}).get("question_type"),
                step=step,
                is_correct=is_correct,
                score=score,
                time_taken=time_taken,
                answer_meta=answer_meta or {},
                ai_eval_meta=ai_eval_meta or {},
                weakness_tags=weakness_tags or [],
                occurred_at=occurred_at,
                progress=progress,
                word=word,
                pack_mastery_pct=pack_mastery_pct,
            )
        except Exception as exc:
            logger.error(
                "vocab personalization write failed user={} event={} word={} err_type={} err={}",
                user_id,
                event_type,
                word_id,
                type(exc).__name__,
                exc,
            )

    async def _get_vocab_words_batch(
        self,
        word_ids: List[str],
        *,
        pack_id: Optional[str] = None,
        mastery_steps: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        if not word_ids:
            return {}
        return await self.lexicon.get_word_dtos_batch(
            word_ids,
            pack_id=pack_id,
            mastery_steps=mastery_steps,
        )

    async def _get_vocab_word(
        self,
        word_id: str,
        *,
        pack_id: Optional[str] = None,
        mastery_step: int = 0,
    ) -> Optional[Dict[str, Any]]:
        batch = await self._get_vocab_words_batch(
            [word_id],
            pack_id=pack_id,
            mastery_steps={word_id: mastery_step},
        )
        return batch.get(word_id)

    async def _list_vocab_words(
        self, *, pack_id: Optional[str] = None, **kwargs
    ) -> List[Dict[str, Any]]:
        if pack_id:
            return await self.lexicon.list_words_for_pack(
                pack_id, limit=kwargs.get("limit", 5000)
            )
        return await self.lexicon.list_lexemes(
            limit=kwargs.get("limit", 50),
            skip=kwargs.get("skip", 0),
        )

    async def _get_vocab_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        return await self.lexicon.get_pack(pack_id)

    async def _list_vocab_packs(self, **kwargs) -> List[Dict[str, Any]]:
        return await self.lexicon.list_packs(**kwargs)

    def _today_key(self) -> str:
        """Calendar today in Vietnam — matches how learners expect 'Today'."""
        return datetime.now(VN_TZ).date().isoformat()

    def _today_keys(self) -> Set[str]:
        """Accept records stamped with VN or UTC 'today' (legacy rows)."""
        vn = datetime.now(VN_TZ).date().isoformat()
        utc = datetime.utcnow().date().isoformat()
        return {vn, utc}

    def _is_seen_today(self, last_seen: Any, last_studied: Any = None) -> bool:
        key = self._normalize_day_key(last_seen)
        if key and key in self._today_keys():
            return True
        if not last_studied:
            return False
        if isinstance(last_studied, datetime):
            studied = last_studied
            if studied.tzinfo is None:
                studied = studied.replace(tzinfo=ZoneInfo("UTC"))
            vn_day = studied.astimezone(VN_TZ).date().isoformat()
            if vn_day in self._today_keys():
                return True
            utc_day = studied.astimezone(ZoneInfo("UTC")).date().isoformat()
            return utc_day in self._today_keys()
        return False

    async def _count_pack_learned_today(self, progress: List[Dict[str, Any]]) -> int:
        """Unique words studied today in a pack (dedupe legacy + lexeme ids)."""
        seen: Set[str] = set()
        count = 0
        for item in progress:
            if not self._is_seen_today(
                item.get("last_seen_date"), item.get("last_studied")
            ):
                continue
            cid = item.get("content_id") or ""
            canon = cid
            if self.lexicon is not None:
                lex_id = await self.lexicon.resolve_word_id(cid)
                if lex_id:
                    canon = str(lex_id)
            if canon in seen:
                continue
            seen.add(canon)
            count += 1
        return count

    def _count_pack_learned_today_fast(self, progress: List[Dict[str, Any]]) -> int:
        """Library list view — dedupe by content_id only (no per-row lexicon lookup)."""
        seen: Set[str] = set()
        count = 0
        for item in progress:
            if not self._is_seen_today(
                item.get("last_seen_date"), item.get("last_studied")
            ):
                continue
            cid = str(item.get("content_id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            count += 1
        return count

    def _summarize_pack_progress(
        self, progress_items: List[Dict[str, Any]], *, total_words: int
    ) -> Dict[str, int]:
        mastered = 0
        learned = 0
        for item in progress_items:
            if item.get("mastery_level") == "mastered" or item.get("marked_known"):
                mastered += 1
            if (
                item.get("marked_known")
                or item.get("translate_passed")
                or int(item.get("mastery_step", 0) or 0) > 0
            ):
                learned += 1
        return {
            "total_words": total_words,
            "learned_words": learned,
            "mastered_words": mastered,
            "learned_today": self._count_pack_learned_today_fast(progress_items),
        }

    def _pack_mastery_pct_list_view(
        self,
        *,
        pack_id: str,
        progress_items: List[Dict[str, Any]],
        total_words: int,
        pack_mastery_row: Dict[str, Any],
    ) -> float:
        if total_words <= 0:
            return 0.0
        today = self._today_key()
        word_sum = sum(float(p.get("mastery_point_pct") or 0) for p in progress_items)
        stored, _ = apply_calendar_decay(
            float(pack_mastery_row.get("stored_pct") or 0),
            pack_mastery_row.get("last_decay_date"),
            today,
        )
        if word_sum > stored + 0.01:
            stored = word_sum
        return round(stored, 2)

    async def _count_words_by_packs(self, pack_ids: List[str]) -> Dict[str, int]:
        if not pack_ids:
            return {}
        return await self.lexicon.count_words_by_packs(pack_ids)

    async def _enrich_vocab_packs_batch(
        self, *, user_id: str, packs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not packs:
            return []
        pack_ids = [str(p["pack_id"]) for p in packs if p.get("pack_id")]
        word_counts, all_progress, stats_doc = await asyncio.gather(
            self._count_words_by_packs(pack_ids),
            self.progress.list_all_vocab(user_id),
            self.stats.get_or_default(user_id),
        )
        pack_mastery_map = (stats_doc.get("vocab_profile") or {}).get(
            "pack_mastery"
        ) or {}
        if not isinstance(pack_mastery_map, dict):
            pack_mastery_map = {}

        progress_by_pack: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        orphan_items: List[Dict[str, Any]] = []
        for item in all_progress:
            pid = (item.get("pack_id") or "").strip()
            if pid:
                progress_by_pack[pid].append(item)
            else:
                orphan_items.append(item)
        if orphan_items:
            orphan_ids = [
                str(p.get("content_id") or "")
                for p in orphan_items
                if p.get("content_id")
            ]
            pack_ids_for_word = await self.lexicon.pack_ids_for_word_ids(orphan_ids)
            for item in orphan_items:
                wid = str(item.get("content_id") or "")
                for pid in pack_ids_for_word.get(wid) or []:
                    if pid in pack_ids:
                        progress_by_pack[pid].append(item)

        enriched: List[Dict[str, Any]] = []
        for pack in packs:
            pack_id = str(pack.get("pack_id") or "")
            if not pack_id:
                continue
            total = int(word_counts.get(pack_id) or 0)
            progress_items = progress_by_pack.get(pack_id, [])
            summary = self._summarize_pack_progress(progress_items, total_words=total)
            pack.update(summary)
            mastery_row = pack_mastery_map.get(pack_id) or {}
            if not isinstance(mastery_row, dict):
                mastery_row = {}
            pack["progress_percentage"] = self._pack_mastery_pct_list_view(
                pack_id=pack_id,
                progress_items=progress_items,
                total_words=total,
                pack_mastery_row=mastery_row,
            )
            enriched.append(pack)
        return enriched

    def _stamp_vocab_progress(
        self,
        state: Dict[str, Any],
        word: Optional[Dict[str, Any]],
        *,
        pack_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = (
            (pack_id or "").strip()
            or ((word or {}).get("pack_id") or "").strip()
            or (state.get("pack_id") or "").strip()
        )
        if resolved:
            state["pack_id"] = resolved
        state["last_seen_date"] = self._today_key()
        return state

    @staticmethod
    def _normalize_day_key(day: Any) -> Optional[str]:
        if not day:
            return None
        if isinstance(day, datetime):
            return day.date().isoformat()
        text = str(day).strip()
        if len(text) >= 10:
            return text[:10]
        return text or None

    def _is_vocab_learned(self, progress: Dict[str, Any]) -> bool:
        if progress.get("marked_known") or progress.get("mastery_level") == "mastered":
            return True
        try:
            if int(progress.get("mastery_step", 0)) > 0:
                return True
        except (TypeError, ValueError):
            pass
        return int(progress.get("total_attempts", 0) or 0) > 0

    async def _vocab_learned_by_category(
        self, progress: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Topic/category counts for learned words (pie chart)."""
        from aiforen.scripts.vocab.pack_specs import infer_stat_labels

        learned = [p for p in progress if self._is_vocab_learned(p)]
        if not learned:
            return []

        ids = list({p["content_id"] for p in learned if p.get("content_id")})
        counts: Dict[str, int] = defaultdict(int)
        chunk_size = 400

        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            meta: Dict[str, Dict[str, Any]] = {}
            meta = await self.lexicon.lookup_labels_for_word_ids(chunk)

            for wid in chunk:
                info = meta.get(wid) or {}
                labels = list(info.get("stat_labels") or [])
                if not labels:
                    lemma = info.get("lemma") or ""
                    labels = infer_stat_labels(lemma) if lemma else []
                if not labels:
                    labels = [info.get("category") or "general"]
                primary = labels[0] if labels else "general"
                counts[primary] += 1

        return [
            {"category": cat, "count": cnt}
            for cat, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        ]

    def _vocab_daily_counts(
        self, stats: Dict[str, Any], progress: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """Per-day vocabulary activity for contribution-style heatmaps."""
        counts: Dict[str, int] = {}
        for item in progress:
            day = self._normalize_day_key(item.get("last_seen_date"))
            if day:
                counts[day] = counts.get(day, 0) + 1
        stored = stats.get("daily_activity") or {}
        if isinstance(stored, dict):
            for day, payload in stored.items():
                day_key = self._normalize_day_key(day)
                if not day_key:
                    continue
                bump = 0
                if isinstance(payload, dict):
                    bump = int(payload.get("vocab") or 0)
                elif isinstance(payload, (int, float)):
                    bump = int(payload)
                if bump > 0:
                    counts[day_key] = max(counts.get(day_key, 0), bump)
        return counts

    def _vocab_daily_mistakes(self, stats: Dict[str, Any]) -> Dict[str, int]:
        """Per-day vocab mistakes for heatmap tooltips."""
        return vocab_mistakes_from_daily_activity(stats.get("daily_activity") or {})

    def _as_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("UTC"))

    def _is_vocab_mastered_item(self, progress: Dict[str, Any]) -> bool:
        return bool(
            progress.get("marked_known") or progress.get("mastery_level") == "mastered"
        )

    def _progress_learned_by(self, progress: Dict[str, Any], end_dt: datetime) -> bool:
        first = progress.get("first_studied")
        if isinstance(first, datetime):
            if self._as_utc(first) > self._as_utc(end_dt):
                return False
        try:
            if float(progress.get("mastery_point_pct") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        return int(progress.get("total_attempts", 0) or 0) > 0

    def _progress_mastered_by(self, progress: Dict[str, Any], end_dt: datetime) -> bool:
        if not self._is_vocab_mastered_item(progress):
            return False
        marker = (
            progress.get("last_studied")
            or progress.get("updated_at")
            or progress.get("first_studied")
        )
        if not isinstance(marker, datetime):
            return False
        return self._as_utc(marker) <= self._as_utc(end_dt)

    def _first_studied_utc(self, progress: Dict[str, Any]) -> Optional[datetime]:
        first = progress.get("first_studied")
        if not isinstance(first, datetime):
            return None
        return self._as_utc(first)

    def _progress_in_week_by(
        self, progress: Dict[str, Any], end_dt: datetime, week_start_utc: datetime
    ) -> bool:
        """Word first studied within the rolling 7-day window ending on end_dt."""
        first = self._first_studied_utc(progress)
        if not first or first > self._as_utc(end_dt) or first < week_start_utc:
            return False
        return self._progress_learned_by(progress, end_dt)

    def _pack_band_info(self, pack: Dict[str, Any]) -> Dict[str, Any]:
        """Stable band bucket + display label for a vocab pack."""
        family = pack.get("pack_family") or "band"
        if family == "cefr":
            category = pack.get("category") or "General"
            return {"key": f"cefr:{category}", "label": category, "band": None}
        if family == "gre":
            return {"key": "gre", "label": "GRE", "band": None}
        band = float(pack.get("source_band_min") or pack.get("target_band_min") or 6.0)
        label = f"Band {int(band) if band == int(band) else band}"
        return {"key": f"band:{band}", "label": label, "band": band}

    def _progress_marker_vn_date(self, progress: Dict[str, Any]) -> Optional[date]:
        last_studied = progress.get("last_studied")
        if isinstance(last_studied, datetime):
            return last_studied.astimezone(VN_TZ).date()
        day_key = self._normalize_day_key(progress.get("last_seen_date"))
        if not day_key:
            return None
        try:
            y, m, d = day_key.split("-")
            return date(int(y), int(m), int(d))
        except (TypeError, ValueError):
            return None

    def _progress_touched_between(
        self, progress: Dict[str, Any], start_day: date, end_day: date
    ) -> bool:
        marker = self._progress_marker_vn_date(progress)
        return bool(marker and start_day <= marker <= end_day)

    def _band_pack_mastery_pct(
        self, packs: List[Dict[str, Any]], pack_ids: Set[str]
    ) -> float:
        """Weighted pack mastery v2 (progress_percentage) for a band bucket."""
        relevant = [p for p in packs if p.get("pack_id") in pack_ids]
        if not relevant:
            return 0.0
        weighted = 0.0
        weight_total = 0
        for pack in relevant:
            learned = int(pack.get("learned_words") or 0)
            if learned <= 0:
                continue
            pct = float(pack.get("progress_percentage") or 0)
            weighted += pct * learned
            weight_total += learned
        if weight_total > 0:
            return round(weighted / weight_total, 2)
        pcts = [float(p.get("progress_percentage") or 0) for p in relevant]
        return round(sum(pcts) / len(pcts), 2) if pcts else 0.0

    def _resolve_active_band_mastery(
        self,
        *,
        progress: List[Dict[str, Any]],
        packs: List[Dict[str, Any]],
        profile: Dict[str, Any],
        week_start_day: date,
        today: date,
    ) -> Dict[str, Any]:
        pack_by_id = {p["pack_id"]: p for p in packs if p.get("pack_id")}
        band_labels: Dict[str, str] = {}
        band_numbers: Dict[str, Optional[float]] = {}
        band_pack_ids: Dict[str, Set[str]] = defaultdict(set)
        for pack in packs:
            info = self._pack_band_info(pack)
            key = info["key"]
            band_labels[key] = info["label"]
            band_numbers[key] = info.get("band")
            band_pack_ids[key].add(pack["pack_id"])

        band_activity: Dict[str, int] = defaultdict(int)
        for item in progress:
            pack_id = item.get("pack_id")
            if not pack_id or pack_id not in pack_by_id:
                continue
            if not self._progress_touched_between(item, week_start_day, today):
                continue
            key = self._pack_band_info(pack_by_id[pack_id])["key"]
            band_activity[key] += 1

        active_key: Optional[str] = None
        source = "overall"
        if band_activity:
            active_key = max(band_activity.items(), key=lambda row: row[1])[0]
            source = "week"
        else:
            target_band = float(profile.get("target_band") or 0)
            if target_band > 0:
                for pack in packs:
                    info = self._pack_band_info(pack)
                    band = info.get("band")
                    if band is not None and abs(band - target_band) < 0.01:
                        active_key = info["key"]
                        source = "target"
                        break
                if active_key is None:
                    active_key = f"band:{target_band}"
                    band_labels.setdefault(
                        active_key,
                        f"Band {int(target_band) if target_band == int(target_band) else target_band}",
                    )
                    band_numbers.setdefault(active_key, target_band)
                    source = "target"

        if not active_key:
            all_pack_ids = {p["pack_id"] for p in packs if p.get("pack_id")}
            now_utc = datetime.now(ZoneInfo("UTC"))
            learned = sum(1 for p in progress if self._progress_learned_by(p, now_utc))
            mastered = sum(
                1 for p in progress if self._progress_mastered_by(p, now_utc)
            )
            return {
                "band_label": "",
                "band": None,
                "mastery_pct": self._band_pack_mastery_pct(packs, all_pack_ids),
                "words_this_week": 0,
                "learned": learned,
                "mastered": mastered,
                "source": "overall",
            }

        pack_ids = band_pack_ids.get(active_key, set())
        now_utc = datetime.now(ZoneInfo("UTC"))
        learned = 0
        mastered = 0
        for item in progress:
            if item.get("pack_id") not in pack_ids:
                continue
            if self._progress_learned_by(item, now_utc):
                learned += 1
            if self._progress_mastered_by(item, now_utc):
                mastered += 1

        return {
            "band_label": band_labels.get(active_key, ""),
            "band": band_numbers.get(active_key),
            "mastery_pct": self._band_pack_mastery_pct(packs, pack_ids),
            "words_this_week": int(band_activity.get(active_key, 0)),
            "learned": learned,
            "mastered": mastered,
            "source": source,
        }

    def _build_vocab_weekly_insights(
        self,
        *,
        daily_counts: Dict[str, int],
        progress: List[Dict[str, Any]],
        packs: Optional[List[Dict[str, Any]]] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Last 7 calendar days (VN): words + band-scoped mastery for charts."""
        packs = packs or []
        profile = profile or {}
        today = datetime.now(VN_TZ).date()
        week_start_day = today - timedelta(days=6)
        week_start = datetime(
            today.year, today.month, today.day, tzinfo=VN_TZ
        ) - timedelta(days=6)
        week_start_utc = week_start.astimezone(ZoneInfo("UTC"))
        days: List[Dict[str, Any]] = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            key = day.isoformat()
            end_dt = datetime(
                day.year, day.month, day.day, 23, 59, 59, tzinfo=VN_TZ
            ).astimezone(ZoneInfo("UTC"))
            learned_cum = sum(
                1 for p in progress if self._progress_learned_by(p, end_dt)
            )
            mastered_cum = sum(
                1 for p in progress if self._progress_mastered_by(p, end_dt)
            )
            mastery_pct = round(mastered_cum / max(1, learned_cum) * 100, 1)
            days.append(
                {
                    "date": key,
                    "label": day.strftime("%a"),
                    "words": int(daily_counts.get(key, 0)),
                    "mastery_pct": mastery_pct,
                }
            )

        def _week_words(start_offset: int) -> int:
            total = 0
            for i in range(7):
                d = today - timedelta(days=start_offset + i)
                total += int(daily_counts.get(d.isoformat(), 0))
            return total

        words_this_week = _week_words(0)
        words_last_week = _week_words(7)
        mastered_this_week = 0
        for item in progress:
            if not self._is_vocab_mastered_item(item):
                continue
            marker = item.get("last_studied") or item.get("updated_at")
            if not isinstance(marker, datetime):
                continue
            if marker.tzinfo is None:
                marker = marker.replace(tzinfo=ZoneInfo("UTC"))
            if marker >= week_start_utc:
                mastered_this_week += 1

        now_utc = datetime.now(ZoneInfo("UTC"))
        learned_now = sum(1 for p in progress if self._progress_learned_by(p, now_utc))
        mastered_now = sum(
            1 for p in progress if self._progress_mastered_by(p, now_utc)
        )
        overall_mastery_rate = round(mastered_now / max(1, learned_now) * 100, 1)

        active_band = self._resolve_active_band_mastery(
            progress=progress,
            packs=packs,
            profile=profile,
            week_start_day=week_start_day,
            today=today,
        )
        display_mastery_rate = (
            active_band["mastery_pct"]
            if active_band.get("band_label")
            else overall_mastery_rate
        )

        for day in days:
            day["mastery_peak_pct"] = day["mastery_pct"]

        return {
            "words_this_week": words_this_week,
            "words_last_week": words_last_week,
            "words_delta": words_this_week - words_last_week,
            "mastered_this_week": mastered_this_week,
            "mastery_rate": display_mastery_rate,
            "overall_mastery_rate": overall_mastery_rate,
            "active_band_mastery": active_band,
            "days": days,
        }

    async def _expand_vocab_content_ids(self, word_ids: List[str]) -> List[str]:
        """Include legacy word_ids so progress rows match PG pack words."""
        expanded: Set[str] = set(word_ids)
        if not word_ids:
            return list(expanded)
        resolved = await self.lexicon.resolve_word_ids(word_ids)
        for wid, lex_id in resolved.items():
            expanded.add(str(lex_id))
            expanded.add(wid)
        try:
            from sqlalchemy import select

            from aiforen.domain.sql_models import VocabLegacyWordMap

            lexeme_ids = list(dict.fromkeys(resolved.values()))
            if lexeme_ids:
                stmt = select(
                    VocabLegacyWordMap.legacy_word_id, VocabLegacyWordMap.lexeme_id
                ).where(VocabLegacyWordMap.lexeme_id.in_(lexeme_ids))
                for legacy_id, lex_id in (await self.lexicon.s.execute(stmt)).all():
                    expanded.add(legacy_id)
                    expanded.add(str(lex_id))
        except Exception as exc:
            logger.warning("Could not expand legacy vocab ids: {}", exc)
        return list(expanded)

    async def _pack_progress_items(
        self, *, user_id: str, pack_id: str, word_ids: List[str]
    ) -> List[Dict[str, Any]]:
        expanded = await self._expand_vocab_content_ids(word_ids)
        return await self.progress.list_for_pack(
            user_id=user_id, pack_id=pack_id, content_ids=expanded
        )

    def _tomorrow_start(self) -> datetime:
        tomorrow = datetime.utcnow().date() + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day)

    def _base_progress(
        self,
        *,
        user_id: str,
        word_id: str,
        now: datetime,
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if existing:
            return dict(existing)
        return {
            "user_id": user_id,
            "content_id": word_id,
            "content_type": "vocabulary",
            "mastery_level": "new",
            "correct_answers": 0,
            "total_attempts": 0,
            "current_streak": 0,
            "best_streak": 0,
            "mastery_step": 0,
            "mastery_point_pct": 0.0,
            "learn_credited": False,
            "mcq_credited": False,
            "best_translate_pct": 0.0,
            "best_topic_pct": 0.0,
            "marked_known": False,
            "spaced_repetition": {
                "ease_factor": 2.5,
                "interval": 0,
                "repetitions": 0,
                "last_reviewed": None,
                "next_review": now,
            },
            "first_studied": now,
            "last_studied": now,
            "updated_at": now,
        }

    def _mastery_step_for(self, progress: Optional[Dict[str, Any]]) -> int:
        """Effective mastery step (0..5), treating marked-known/mastered as fully mastered."""

        if not progress:
            return 0
        if progress.get("marked_known") or progress.get("mastery_level") == "mastered":
            return 5
        try:
            return max(0, min(5, int(progress.get("mastery_step", 0))))
        except (TypeError, ValueError):
            return 0

    def _lazy_migrate_word_progress(
        self, state: Dict[str, Any], pack_total_words: int
    ) -> None:
        if state.get("mastery_point_pct") is not None and state.get(
            "mastery_migrated_v2"
        ):
            return
        pts = float(state.get("mastery_point_pct") or 0.0)
        if pts <= 0:
            pts = migrate_legacy_word_points(state, pack_total_words)
        state["mastery_point_pct"] = pts
        state["mastery_migrated_v2"] = True
        if state.get("learn_passed") and not state.get("learn_credited"):
            state["learn_credited"] = True
        if (state.get("last_mcq_result") or {}).get("is_correct") and not state.get(
            "mcq_credited"
        ):
            state["mcq_credited"] = True

    @staticmethod
    def _clear_vocab_cycle_flags(state: Dict[str, Any]) -> None:
        state["learn_passed"] = False
        state["learn_credited"] = False
        state["mcq_credited"] = False
        state["quiz_slots_credited"] = []
        state["translate_passed"] = False
        state["topic_passed"] = False
        state["best_translate_pct"] = 0.0
        state["best_topic_pct"] = 0.0

    def _bump_vocab_mastery(self, state: Dict[str, Any]) -> int:
        """Advance mastery step (0..5) after a successful recall or production step."""

        try:
            current = max(0, min(5, int(state.get("mastery_step", 0))))
        except (TypeError, ValueError):
            current = 0
        next_step = min(5, current + 1)
        if next_step == current:
            return current
        state["mastery_step"] = next_step
        state["mastery_level"] = (
            "mastered"
            if next_step >= 5
            else ("reviewing" if next_step >= 2 else "learning")
        )
        return next_step

    async def _pack_total_words(self, pack_id: str) -> int:
        words = await self._list_vocab_words(pack_id=pack_id, limit=5000)
        return max(1, len(words))

    async def _pack_mastery_after_credit(self, user_id: str, pack_id: str) -> float:
        row = await self.stats.get_pack_mastery(user_id, pack_id)
        return round(float(row["stored_pct"]), 2)

    async def _resolve_pack_mastery_pct(
        self,
        *,
        user_id: str,
        pack_id: str,
        progress_items: List[Dict[str, Any]],
        total_words: int,
    ) -> float:
        for p in progress_items:
            self._lazy_migrate_word_progress(p, total_words)
        word_sum = sum(float(p.get("mastery_point_pct") or 0) for p in progress_items)
        row = await self.stats.get_pack_mastery(user_id, pack_id)
        today = self._today_key()
        stored, decay_date = apply_calendar_decay(
            float(row["stored_pct"]),
            row.get("last_decay_date"),
            today,
        )
        if word_sum > stored + 0.01:
            stored = word_sum
        await self.stats.set_pack_mastery(
            user_id, pack_id, stored_pct=stored, last_decay_date=decay_date
        )
        return round(stored, 2)

    async def _credit_pack_and_word(
        self,
        *,
        user_id: str,
        pack_id: str,
        state: Dict[str, Any],
        delta_pct: float,
        pack_total_words: int,
    ) -> float:
        pack_id = (pack_id or "").strip()
        if delta_pct <= 0 or not pack_id:
            return 0.0
        self._lazy_migrate_word_progress(state, pack_total_words)
        state["mastery_point_pct"] = (
            float(state.get("mastery_point_pct") or 0) + delta_pct
        )
        state["pack_id"] = pack_id
        stored = await self.stats.add_pack_mastery_delta(
            user_id, pack_id, delta_pct, today_key=self._today_key()
        )
        return round(float(stored), 2)

    async def _withdraw_word_cycle_points(
        self,
        *,
        user_id: str,
        pack_id: str,
        state: Dict[str, Any],
    ) -> None:
        lost = float(state.get("mastery_point_pct") or 0)
        if lost > 0:
            row = await self.stats.get_pack_mastery(user_id, pack_id)
            today = self._today_key()
            stored, decay_date = apply_calendar_decay(
                float(row["stored_pct"]),
                row.get("last_decay_date"),
                today,
            )
            stored = max(0.0, stored - lost)
            await self.stats.set_pack_mastery(
                user_id, pack_id, stored_pct=stored, last_decay_date=decay_date
            )
        state["mastery_point_pct"] = 0.0

    def _decorate_vocab_word(
        self,
        word: Dict[str, Any],
        progress: Optional[Dict[str, Any]],
        *,
        pack_total_words: int = 12,
    ) -> Dict[str, Any]:
        out = dict(word)
        prog = dict(progress or {})
        self._lazy_migrate_word_progress(prog, pack_total_words)
        pts = float(prog.get("mastery_point_pct") or 0)
        budget = word_budget_pct(pack_total_words)
        step = int(prog.get("mastery_step", 0))
        out["progress"] = {
            "mastery_step": step,
            "display_step": display_step_for_word(pts, pack_total_words),
            "mastery_point_pct": round(pts, 4),
            "word_budget_pct": round(budget, 4),
            "mastery_level": prog.get("mastery_level", "new"),
            "next_review": (prog.get("spaced_repetition") or {}).get("next_review"),
            "marked_known": bool(prog.get("marked_known", False)),
            "last_seen_date": prog.get("last_seen_date"),
            "learn_passed": bool(
                prog.get("learn_credited") or prog.get("learn_passed")
            ),
            "translate_passed": bool(prog.get("translate_passed", False)),
            "topic_passed": bool(prog.get("topic_passed", False)),
        }
        return out

    # ---------- content ----------

    async def list_grammar(self, **kwargs) -> List[Dict[str, Any]]:
        return await self.grammar.list(**kwargs)

    async def list_vocab(self, **kwargs) -> List[Dict[str, Any]]:
        return await self.lexicon.list_lexemes(
            limit=kwargs.get("limit", 50),
            skip=kwargs.get("skip", 0),
        )

    async def get_content(
        self, content_type: str, content_id: str
    ) -> Optional[Dict[str, Any]]:
        if content_type == "grammar":
            return await self.grammar.get(content_id)
        if content_type == "vocabulary":
            return await self._get_vocab_word(content_id)
        return None

    async def categories(self, content_type: str) -> Dict[str, Any]:
        if content_type == "grammar":
            cats = await self.grammar.categories()
        else:
            cats = await self.lexicon.categories()
        return {c: {"name": c, "description": ""} for c in cats}

    async def category_progress(
        self, content_type: str, user_id: str
    ) -> List[Dict[str, Any]]:
        items = await self.progress.list_for_user(user_id, content_type=content_type)
        if content_type == "grammar":
            all_items = await self.grammar.list(limit=500)
        else:
            all_items = await self.lexicon.list_lexemes(limit=500)

        per_category: Dict[str, Dict[str, Any]] = {}
        for it in all_items:
            cat = it.get("category", "general")
            per_category.setdefault(
                cat,
                {
                    "category": cat,
                    "description": "",
                    "importance": "Important",
                    "total_structures": 0,
                    "learned_structures": 0,
                    "mastered_structures": 0,
                    "progress_percentage": 0.0,
                },
            )
            per_category[cat]["total_structures"] += 1

        learned_ids = {p["content_id"] for p in items if p.get("total_attempts", 0) > 0}
        mastered_ids = {
            p["content_id"] for p in items if p.get("mastery_level") == "mastered"
        }

        for it in all_items:
            cat = it.get("category", "general")
            if it.get("structure_id") or it.get("word_id"):
                cid = it.get("structure_id") or it.get("word_id")
                if cid in learned_ids:
                    per_category[cat]["learned_structures"] += 1
                if cid in mastered_ids:
                    per_category[cat]["mastered_structures"] += 1

        out = []
        for c in per_category.values():
            total = c["total_structures"] or 1
            c["progress_percentage"] = round(c["learned_structures"] / total * 100, 1)
            out.append(c)
        return out

    # ---------- progress ----------

    async def list_progress(self, user_id: str, **kwargs) -> List[Dict[str, Any]]:
        return await self.progress.list_for_user(user_id, **kwargs)

    async def update_progress(
        self,
        *,
        user_id: str,
        content_id: str,
        content_type: str,
        is_correct: bool,
        time_taken: int,
        exercise_type: str,
    ) -> Dict[str, Any]:
        result = await self.progress.upsert_review(
            user_id=user_id,
            content_id=content_id,
            content_type=content_type,
            is_correct=is_correct,
            time_taken=time_taken,
            exercise_type=exercise_type,
        )
        await self.stats.bump(
            user_id,
            content_type=content_type,
            is_correct=is_correct,
            time_taken=time_taken,
        )
        return result

    async def due(
        self, user_id: str, content_type: str, limit: int
    ) -> List[Dict[str, Any]]:
        return await self.progress.due_for_review(user_id, content_type, limit)

    # ---------- stats ----------

    async def get_stats(self, user_id: str) -> Dict[str, Any]:
        return await self.stats.get_or_default(user_id)

    # ---------- new vocabulary flow ----------

    async def get_vocab_profile(self, user_id: str) -> Dict[str, Any]:
        stats = await self.stats.get_or_default(user_id)
        profile = stats.get("vocab_profile") or {}
        current_band = float(profile.get("current_band", 6.0))
        target_band = float(profile.get("target_band", 7.0))
        daily_goal = int(profile.get("daily_goal", 5))
        packs = await self.list_vocab_packs(
            user_id=user_id, current_band=current_band, target_band=target_band
        )
        return {
            "current_band": current_band,
            "target_band": target_band,
            "daily_goal": daily_goal,
            "calibration_completed": bool(profile.get("calibration_completed")),
            "calibration_cefr_level": profile.get("calibration_cefr_level"),
            "suggested_packs": packs,
        }

    async def update_vocab_profile(
        self,
        user_id: str,
        *,
        current_band: float,
        target_band: float,
        daily_goal: int = 5,
    ) -> Dict[str, Any]:
        await self.stats.update_vocab_profile(
            user_id,
            current_band=current_band,
            target_band=target_band,
            daily_goal=daily_goal,
        )
        return await self.get_vocab_profile(user_id)

    async def list_vocab_packs(
        self,
        *,
        user_id: str,
        current_band: Optional[float] = None,
        target_band: Optional[float] = None,
        all_packs: bool = False,
    ) -> List[Dict[str, Any]]:
        if not all_packs and (current_band is None or target_band is None):
            profile = (await self.stats.get_or_default(user_id)).get(
                "vocab_profile"
            ) or {}
            current_band = float(profile.get("current_band", 6.0))
            target_band = float(profile.get("target_band", 7.0))
        packs = await self._list_vocab_packs(
            current_band=None if all_packs else current_band,
            target_band=None if all_packs else target_band,
        )
        return await self._enrich_vocab_packs_batch(user_id=user_id, packs=packs)

    async def get_vocab_session(
        self,
        *,
        user_id: str,
        pack_id: str,
        limit: int = 5,
        word_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        pack = await self._get_vocab_pack(pack_id)
        if not pack:
            return {"pack": None, "words": [], "count": 0}

        requested_ids = [
            str(wid).strip() for wid in (word_ids or []) if str(wid).strip()
        ]
        cap = max(1, min(limit, 20))

        if requested_ids:
            # Count via aggregate instead of hydrating up to 500 full word DTOs.
            pack_total = (await self._count_words_by_packs([pack_id])).get(pack_id, 0)
            progress_items = await self._pack_progress_items(
                user_id=user_id, pack_id=pack_id, word_ids=requested_ids
            )
            progress_by_id: Dict[str, Dict[str, Any]] = {}
            alias_to_progress: Dict[str, Dict[str, Any]] = {}
            content_ids = [p["content_id"] for p in progress_items]
            id_map = (
                await self.lexicon.resolve_word_ids(content_ids)
                if self.lexicon is not None
                else {}
            )
            for p in progress_items:
                cid = p["content_id"]
                alias_to_progress[cid] = p
                lex_id = id_map.get(cid)
                if lex_id:
                    alias_to_progress[str(lex_id)] = p
            request_map = (
                await self.lexicon.resolve_word_ids(requested_ids[:cap])
                if self.lexicon is not None
                else {}
            )
            selected_wids: List[str] = []
            mastery_steps: Dict[str, int] = {}
            for wid in requested_ids[:cap]:
                progress = alias_to_progress.get(wid)
                if progress is None:
                    lex_id = request_map.get(wid)
                    if lex_id:
                        progress = alias_to_progress.get(str(lex_id))
                if progress and (
                    progress.get("marked_known")
                    or progress.get("mastery_level") == "mastered"
                ):
                    continue
                selected_wids.append(wid)
                mastery_steps[wid] = self._mastery_step_for(progress)
            hydrated_map = await self._get_vocab_words_batch(
                selected_wids,
                pack_id=pack_id,
                mastery_steps=mastery_steps,
            )
            decorated: List[Dict[str, Any]] = []
            for wid in selected_wids:
                hydrated = hydrated_map.get(wid)
                if not hydrated:
                    continue
                progress = alias_to_progress.get(wid)
                if progress is None:
                    lex_id = request_map.get(wid)
                    if lex_id:
                        progress = alias_to_progress.get(str(lex_id))
                decorated.append(
                    self._decorate_vocab_word(
                        hydrated, progress, pack_total_words=pack_total
                    )
                )
            return {"pack": pack, "words": decorated, "count": len(decorated)}

        words = await self._list_vocab_words(pack_id=pack_id, limit=500)
        word_ids = [w["word_id"] for w in words]
        progress_items = await self._pack_progress_items(
            user_id=user_id, pack_id=pack_id, word_ids=word_ids
        )
        progress_by_id: Dict[str, Dict[str, Any]] = {}
        alias_to_progress: Dict[str, Dict[str, Any]] = {}
        progress_content_ids = [p["content_id"] for p in progress_items]
        all_ids = list({*progress_content_ids, *word_ids})
        id_map = (
            await self.lexicon.resolve_word_ids(all_ids)
            if self.lexicon is not None
            else {}
        )
        for p in progress_items:
            cid = p["content_id"]
            alias_to_progress[cid] = p
            lex_id = id_map.get(cid)
            if lex_id:
                alias_to_progress[str(lex_id)] = p
        for wid in word_ids:
            row = alias_to_progress.get(wid)
            if row is None:
                lex_id = id_map.get(wid)
                if lex_id:
                    row = alias_to_progress.get(str(lex_id))
            if row is not None:
                progress_by_id[wid] = row

        due: List[Dict[str, Any]] = []
        new: List[Dict[str, Any]] = []
        for word in words:
            progress = progress_by_id.get(word["word_id"])
            if progress and (
                progress.get("marked_known")
                or progress.get("mastery_level") == "mastered"
            ):
                continue
            if progress and self._is_seen_today(
                progress.get("last_seen_date"), progress.get("last_studied")
            ):
                continue
            locked_until = progress.get("failed_locked_until") if progress else None
            if locked_until and locked_until > now:
                continue
            next_review = _as_utc_aware(
                ((progress or {}).get("spaced_repetition") or {}).get("next_review")
            )
            if progress and next_review and next_review <= now:
                due.append(word)
            elif not progress:
                new.append(word)

        random.shuffle(due)
        random.shuffle(new)
        selected = (due + new)[: max(1, min(limit, 20))]

        pack_total = len(word_ids)
        mastery_steps = {
            word["word_id"]: self._mastery_step_for(progress_by_id.get(word["word_id"]))
            for word in selected
        }
        hydrated_map = await self._get_vocab_words_batch(
            [word["word_id"] for word in selected],
            pack_id=pack_id,
            mastery_steps=mastery_steps,
        )
        decorated = []
        for word in selected:
            wid = word["word_id"]
            progress = progress_by_id.get(wid)
            hydrated = hydrated_map.get(wid) or word
            decorated.append(
                self._decorate_vocab_word(
                    hydrated, progress, pack_total_words=pack_total
                )
            )

        # NOTE: we deliberately do NOT mark `last_seen_date` here so that
        # reloading the page returns the same queue.  The date is set when
        # the user actually answers an MCQ, submits a sentence, or marks
        # the word as known.
        return {"pack": pack, "words": decorated, "count": len(decorated)}

    async def get_vocab_calibration_words(
        self,
        *,
        user_id: str,
        limit: int = 32,
    ) -> Dict[str, Any]:
        from aiforen.domain.quick_vocab_check import (
            calibration_words_payload,
            normalize_check_size,
        )

        _ = user_id
        return calibration_words_payload(normalize_check_size(limit))

    def _calibration_level_label(self, level: int, *, locale: str) -> str:
        str(locale).lower().startswith("vi")
        labels_vi = {0: "New", 1: "Seen", 2: "Know", 3: "Use"}
        return labels_vi.get(level, "New")

    def _build_calibration_rule_review(
        self,
        *,
        clean: List[Dict[str, Any]],
        locale: str,
        packs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        rule = estimate_cefr_from_answers(clean)
        cefr = str(rule["cefr_level"])
        vi = str(locale).lower().startswith("vi")

        def _level(answer: Dict[str, Any]) -> int:
            try:
                return max(0, min(3, int(answer.get("level") or 0)))
            except Exception:
                return 0

        known = [a for a in clean if _level(a) >= 2]
        confident = [a for a in clean if _level(a) >= 3]

        strengths: List[str] = []
        weak_spots: List[str] = []
        if known:
            strengths.append(
                f"Bạn biết hoặc nhận ra {len(known)}/{len(clean)} từ trong bài check."
                if vi
                else f"You recognized {len(known)}/{len(clean)} check words."
            )
        if confident:
            strengths.append(
                f"{len(confident)} từ bạn đánh dấu dùng được trong câu."
                if vi
                else f"{len(confident)} words marked as usable in sentences."
            )
        if len(confident) < max(2, len(clean) // 4):
            weak_spots.append(
                "Chưa nhiều từ ở mức Use — cần luyện viết câu ngắn sớm."
                if vi
                else "Few words at Use level — add short sentence practice."
            )
        uncertain = [a for a in clean if _level(a) <= 1]
        if len(uncertain) >= 2:
            weak_spots.append(
                "Một số từ mức New/Seen cần recall trước khi học thêm từ mới."
                if vi
                else "Some New/Seen words need recall before adding new words."
            )

        recommended_plan = [
            {
                "title": f"Bắt đầu pack {cefr}" if vi else f"Start {cefr} pack",
                "description": (
                    "Học 8 từ, ưu tiên hiểu nghĩa và tự nhớ lại."
                    if vi
                    else "Learn 8 words with active recall."
                ),
            },
            {
                "title": "Repair từ chưa chắc" if vi else "Repair uncertain words",
                "description": (
                    "Làm MCQ với từ mức New/Seen."
                    if vi
                    else "Redo MCQ for New/Seen words."
                ),
            },
            {
                "title": "Viết 3 câu ngắn" if vi else "Write 3 short sentences",
                "description": (
                    "Dùng từ mức Know/Use trong câu IELTS-style."
                    if vi
                    else "Use Know/Use words in short sentences."
                ),
            },
        ]

        ielts_hint = cefr_to_ielts_mid(cefr)
        return {
            "headline": (
                f"Trình độ từ vựng khoảng {cefr}" if vi else f"Vocabulary around {cefr}"
            ),
            "summary": (
                f"Mức CEFR ước lượng {cefr} (tham chiếu IELTS khoảng {ielts_hint:.1f}). "
                "Bắt đầu pack phù hợp và ôn từ chưa chắc."
                if vi
                else f"Estimated CEFR {cefr} (IELTS ref ~{ielts_hint:.1f}). Start a matching pack and repair gaps."
            ),
            "cefr_level": cefr,
            "estimated_band": ielts_hint,
            "ielts_band_hint": ielts_hint,
            "confidence": float(rule["confidence"]),
            "strengths": strengths[:2],
            "weak_spots": weak_spots[:2],
            "recommended_plan": recommended_plan,
            "recommended_pack_id": pick_recommended_pack_id(cefr, packs),
            "source": "fallback_rules",
        }

    def _calibration_insight_coach_lines(
        self,
        *,
        headline: str,
        summary: str,
        strengths: List[str],
        weak_spots: List[str],
        locale: str,
    ) -> List[str]:
        lines: List[str] = []
        if summary:
            lines.append(summary[:160])
        for item in (strengths or [])[:1]:
            if item:
                lines.append(str(item)[:160])
        for item in (weak_spots or [])[:1]:
            if item:
                lines.append(str(item)[:160])
        if len(lines) < 2 and headline:
            lines.insert(0, headline[:120])
        if len(lines) < 2:
            lines.extend(
                build_coach_overview_lines(
                    rhythm="new",
                    locale=locale,
                    streak=0,
                    active_days_14=0,
                    total_progress_words=0,
                    learned_today=0,
                    due_today=0,
                )[: 2 - len(lines)]
            )
        return lines[:3]

    def _build_calibration_llm_context(
        self,
        *,
        answers: List[Dict[str, Any]],
        rule_result: Dict[str, Any],
        locale: str,
        check_size: int,
    ) -> Dict[str, Any]:
        def _level(answer: Dict[str, Any]) -> int:
            try:
                return max(0, min(3, int(answer.get("level") or 0)))
            except Exception:
                return 0

        known = sum(1 for a in answers if _level(a) >= 2)
        use_count = sum(1 for a in answers if _level(a) >= 3)
        word_examples_by_level = {
            "new": [str(a.get("word") or "") for a in answers if _level(a) == 0][:8],
            "seen": [str(a.get("word") or "") for a in answers if _level(a) == 1][:8],
            "know": [str(a.get("word") or "") for a in answers if _level(a) == 2][:8],
            "use": [str(a.get("word") or "") for a in answers if _level(a) == 3][:8],
        }
        answer_distribution = {
            "new": len(word_examples_by_level["new"]),
            "seen": len(word_examples_by_level["seen"]),
            "know": len(word_examples_by_level["know"]),
            "use": len(word_examples_by_level["use"]),
        }
        return {
            "locale": locale,
            "check_size": check_size,
            "copy_goal": "activation_after_calibration",
            "ui_surface": "compact_modal_result",
            "rule_estimate": {
                "cefr_level": rule_result.get("cefr_level"),
                "estimated_vocab_level": rule_result.get("estimated_vocab_level"),
                "estimated_band": rule_result.get("estimated_band"),
                "ielts_band_hint": rule_result.get("ielts_band_hint"),
                "confidence": rule_result.get("confidence"),
                "known_count": known,
                "use_count": use_count,
                "answer_count": len(answers),
                "bands": (rule_result.get("bands") or [])[:6],
                "strengths": rule_result.get("strengths") or [],
                "weak_spots": rule_result.get("weak_spots") or [],
                "recommended_pack_id": rule_result.get("recommended_pack_id"),
            },
            "answer_distribution": answer_distribution,
            "word_examples_by_level": word_examples_by_level,
            "sample_answers": [
                {
                    "word": a.get("word"),
                    "level": a.get("level"),
                    "cefr": a.get("calibration_cefr_level")
                    or a.get("calibration_label"),
                }
                for a in answers[:24]
            ],
        }

    def _mission_payload_from_calibration_insight(
        self,
        insight: Dict[str, Any],
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidate_packs = context.get("candidate_packs") or []
        primary_pack_id = insight.get("recommended_pack_id")
        if not primary_pack_id and candidate_packs:
            primary_pack_id = candidate_packs[0].get("pack_id")
        plan_blocks = []
        for item in insight.get("recommended_plan") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            desc = str(item.get("description") or "").strip()
            if title:
                plan_blocks.append(
                    {
                        "type": "study_pack",
                        "title": title,
                        "description": desc,
                        "target_count": 8,
                        "pack_id": primary_pack_id,
                    }
                )
        return {
            "headline": insight.get("headline") or "",
            "summary": insight.get("summary") or "",
            "confidence": float(insight.get("confidence") or 0.7),
            "plan_blocks": plan_blocks[:3],
            "coach_overview_lines": insight.get("coach_overview_lines") or [],
            "primary_cta": {
                "action_type": "study_pack",
                "label": "Bắt đầu 8 từ",
                "pack_id": primary_pack_id,
                "target_count": 8,
                "session_mode": "full",
            },
        }

    async def review_vocab_calibration(
        self,
        *,
        user_id: str,
        answers: List[Dict[str, Any]],
        locale: str = "vi",
        check_size: int = 32,
    ) -> Dict[str, Any]:
        from aiforen.domain.quick_vocab_check import (
            build_calibration_review,
            get_check_preset,
            normalize_check_size,
        )

        preset = get_check_preset(check_size)
        size = normalize_check_size(check_size)
        clean = [a for a in answers if str(a.get("word") or "").strip()]
        if not clean:
            return {
                "headline": "Chưa đủ dữ liệu",
                "summary": f"Hãy chọn mức độ biết cho đủ {preset.size} từ để nhận kết quả.",
                "estimated_vocab_level": "Below A2",
                "cefr_level": "A2",
                "estimated_band": 5.0,
                "ielts_band_hint": 5.0,
                "confidence": 0.2,
                "confidence_label": "Very low",
                "is_suspicious": False,
                "flags": [],
                "bands": [],
                "strengths": [],
                "weak_spots": ["Chưa có tín hiệu từ quick check."],
                "recommended_plan": [],
                "recommended_pack_id": None,
                "source": "quick_vocab_check_rules",
            }

        packs = await self.list_vocab_packs(user_id=user_id, all_packs=True)
        rule_result = build_calibration_review(
            clean, locale=locale, packs=packs, check_size=size
        )
        result = dict(rule_result)
        base_cefr = str(result.get("cefr_level") or "B1")
        ielts_mid = cefr_to_ielts_mid(base_cefr)
        result.setdefault("estimated_band", ielts_mid)
        result.setdefault("ielts_band_hint", ielts_mid)

        llm_source = "quick_vocab_check_rules"
        llm_context = self._build_calibration_llm_context(
            answers=clean,
            rule_result=rule_result,
            locale=locale,
            check_size=size,
        )
        try:
            provider = get_llm_provider()
            raw_llm = await provider.generate_vocab_calibration_review(
                context=llm_context
            )
            from aiforen.integrations.llm.json_utils import (
                postprocess_calibration_llm_payload,
            )

            llm_payload = postprocess_calibration_llm_payload(
                normalize_vocab_calibration_payload(raw_llm, context=llm_context),
                context=llm_context,
                rule_result=result,
            )
            result["headline"] = llm_payload.get("headline") or result.get("headline")
            result["summary"] = llm_payload.get("summary") or result.get("summary")
            result["cefr_level"] = base_cefr
            result["strengths"] = llm_payload.get("strengths") or result.get(
                "strengths"
            )
            result["weak_spots"] = llm_payload.get("weak_spots") or result.get(
                "weak_spots"
            )
            if llm_payload.get("recommended_plan"):
                result["recommended_plan"] = llm_payload["recommended_plan"]
            result["confidence"] = llm_payload.get(
                "confidence", result.get("confidence")
            )
            if llm_payload.get("primary_cta_label"):
                result["primary_cta_label"] = llm_payload["primary_cta_label"]
            llm_source = "llm_generated"
        except Exception as exc:
            logger.error(
                "vocab calibration LLM failed user={} err_type={} err={}",
                user_id,
                type(exc).__name__,
                exc,
            )

        result["source"] = llm_source
        result["cefr_level"] = base_cefr
        if not result.get("primary_cta_label"):
            try:
                band_label = float(result.get("estimated_band") or ielts_mid)
            except Exception:
                band_label = ielts_mid
            result["primary_cta_label"] = f"Start Band {band_label:.1f} path"
        coach_lines = self._calibration_insight_coach_lines(
            headline=str(result.get("headline") or ""),
            summary=str(result.get("summary") or ""),
            strengths=list(result.get("strengths") or []),
            weak_spots=list(result.get("weak_spots") or []),
            locale=locale,
        )
        calibration_insight = {
            "headline": result.get("headline"),
            "summary": result.get("summary"),
            "cefr_level": result.get("cefr_level"),
            "estimated_vocab_level": result.get("estimated_vocab_level"),
            "confidence": result.get("confidence"),
            "strengths": result.get("strengths") or [],
            "weak_spots": result.get("weak_spots") or [],
            "recommended_plan": result.get("recommended_plan") or [],
            "recommended_pack_id": result.get("recommended_pack_id"),
            "coach_overview_lines": coach_lines,
            "source": llm_source,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

        await self.stats.mark_vocab_calibration_completed(
            user_id,
            cefr_level=str(result.get("cefr_level") or base_cefr),
            estimated_band=float(
                result.get("estimated_band")
                or result.get("ielts_band_hint")
                or ielts_mid
            ),
            calibration_insight=calibration_insight,
        )
        if self.personalization is not None:
            await self.personalization.delete_daily_missions(
                user_id=user_id,
                mission_date=datetime.now(VN_TZ).date(),
            )
        return result

    async def reset_user_learning_data(self, *, user_id: str) -> Dict[str, Any]:
        """Clear learner history; keep account + subscription. User redoes vocab onboarding."""
        cleared = {
            "user_stats": await self.stats.reset_for_user(user_id),
            "learning_progress": await self.progress.delete_all_for_user(user_id),
            "vocab_attempts": await self.vocab_attempts.delete_all_for_user(user_id),
            "writing_submissions": await self.writing_submissions.delete_all_for_user(
                user_id
            ),
        }
        personalization_cleared = await self.personalization.reset_user_learning_data(
            user_id
        )

        fresh_stats = await self.stats.get_or_default(user_id)
        profile = fresh_stats.get("vocab_profile") or {}

        return {
            "reset_at": datetime.utcnow().isoformat() + "Z",
            "calibration_completed": bool(profile.get("calibration_completed")),
            "cleared": {**cleared, **personalization_cleared},
        }

    async def mark_vocab_known(self, *, user_id: str, word_id: str) -> Dict[str, Any]:
        now = datetime.utcnow()
        word = await self._get_vocab_word(word_id)
        existing = await self.progress.get_one(
            user_id=user_id, content_id=word_id, content_type="vocabulary"
        )
        state = self._base_progress(
            user_id=user_id, word_id=word_id, now=now, existing=existing
        )
        state.update(
            {
                "mastery_level": "mastered",
                "mastery_step": 5,
                "marked_known": True,
                "last_studied": now,
                "updated_at": now,
            }
        )
        pack_id = word.get("pack_id") if word else ""
        pack_total = await self._pack_total_words(pack_id) if pack_id else 12
        budget = word_budget_pct(pack_total)
        prev_pts = float(state.get("mastery_point_pct") or 0)
        delta = max(0.0, budget - prev_pts)
        state["mastery_point_pct"] = budget
        state["learn_credited"] = True
        state["mcq_credited"] = True
        state["translate_passed"] = True
        state["topic_passed"] = True
        self._stamp_vocab_progress(state, word)
        saved = await self.progress.upsert_vocab_state(
            user_id=user_id, word_id=word_id, update_doc=state
        )
        pack_pct = 0.0
        if pack_id and delta > 0:
            pack_pct = round(
                await self.stats.add_pack_mastery_delta(
                    user_id, pack_id, delta, today_key=self._today_key()
                ),
                2,
            )
        elif pack_id:
            pack_pct = await self._pack_mastery_after_credit(user_id, pack_id)
        await self._record_vocab_personalization(
            user_id=user_id,
            event_type="mark_known",
            word_id=word_id,
            word=word,
            progress=saved,
            pack_mastery_pct=pack_pct,
            step="mark_known",
            is_correct=True,
            occurred_at=now,
        )
        return {
            "progress": saved,
            "mastery_delta_pct": round(delta, 2),
            "pack_mastery_pct": pack_pct,
        }

    async def forgot_vocab_word(self, *, user_id: str, word_id: str) -> Dict[str, Any]:
        """Reset mastery to 0/5 so the learner can study the word again."""

        now = datetime.utcnow()
        existing = await self.progress.get_one(
            user_id=user_id, content_id=word_id, content_type="vocabulary"
        )
        state = self._base_progress(
            user_id=user_id, word_id=word_id, now=now, existing=existing
        )
        state.update(
            {
                "mastery_level": "new",
                "mastery_step": 0,
                "marked_known": False,
                "current_streak": 0,
                "failed_locked_until": None,
                "last_seen_date": None,
                "last_mcq_result": None,
                "last_studied": now,
                "updated_at": now,
                "spaced_repetition": {
                    "ease_factor": 2.5,
                    "interval": 0,
                    "repetitions": 0,
                    "last_reviewed": None,
                    "next_review": now,
                },
            }
        )
        pack_id = (existing or {}).get("pack_id") or ""
        if not pack_id:
            w = await self._get_vocab_word(word_id)
            pack_id = (w or {}).get("pack_id") or ""
        if pack_id:
            await self._withdraw_word_cycle_points(
                user_id=user_id, pack_id=pack_id, state=state
            )
        self._clear_vocab_cycle_flags(state)
        saved = await self.progress.upsert_vocab_state(
            user_id=user_id, word_id=word_id, update_doc=state
        )
        pack_pct = (
            await self._pack_mastery_after_credit(user_id, pack_id) if pack_id else 0.0
        )
        await self._record_vocab_personalization(
            user_id=user_id,
            event_type="forgot",
            word_id=word_id,
            word=await self._get_vocab_word(word_id) if not pack_id else None,
            progress=saved,
            pack_mastery_pct=pack_pct,
            step="forgot",
            is_correct=False,
            weakness_tags=["stale_review_due"],
            occurred_at=now,
        )
        return {
            "progress": saved,
            "mastery_delta_pct": 0.0,
            "pack_mastery_pct": pack_pct,
        }

    async def submit_vocab_learn_recall(
        self, *, user_id: str, word_id: str, pack_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record Step 1 typing recall (small pack mastery weight)."""

        now = datetime.utcnow()
        word = await self._get_vocab_word(word_id, pack_id=pack_id)
        if not word:
            return {"word_id": word_id, "progress": None}

        existing = await self.progress.get_one(
            user_id=user_id, content_id=word_id, content_type="vocabulary"
        )
        pack_id = (pack_id or word.get("pack_id") or "").strip()
        pack_total = await self._pack_total_words(pack_id) if pack_id else 12
        state = self._base_progress(
            user_id=user_id, word_id=word_id, now=now, existing=existing
        )
        delta = 0.0
        pack_stored = 0.0
        if not state.get("learn_credited"):
            state["learn_passed"] = True
            state["learn_credited"] = True
            delta = delta_learn(pack_total)
            self._stamp_vocab_progress(state, word, pack_id=pack_id or None)
            state["last_studied"] = now
            state["updated_at"] = now
            pack_stored = await self._credit_pack_and_word(
                user_id=user_id,
                pack_id=pack_id,
                state=state,
                delta_pct=delta,
                pack_total_words=pack_total,
            )
            saved = await self.progress.upsert_vocab_state(
                user_id=user_id, word_id=word_id, update_doc=state
            )
        else:
            saved = state
        pack_pct = (
            pack_stored
            if pack_stored > 0
            else (
                await self._pack_mastery_after_credit(user_id, pack_id)
                if pack_id
                else 0.0
            )
        )
        await self._record_vocab_personalization(
            user_id=user_id,
            event_type="learn_recall",
            word_id=word_id,
            word=word,
            progress=saved,
            pack_mastery_pct=pack_pct,
            step="learn_recall",
            is_correct=True,
            occurred_at=now,
        )
        return {
            "word_id": word_id,
            "progress": saved,
            "mastery_delta_pct": round(delta, 2),
            "pack_mastery_pct": pack_pct,
        }

    @staticmethod
    def _grade_vocab_quiz_answer(
        question: Any,
        *,
        selected_option_id: Optional[str],
        free_text_answer: Optional[str],
        reorder_order: Optional[List[int]],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        payload = question.payload if isinstance(question.payload, dict) else {}
        interaction = (question.interaction_kind or "mcq").strip().lower()
        answer_meta: Dict[str, Any] = {"interaction_kind": interaction}

        if interaction == "mcq":
            correct = (question.correct_option_id or "").strip().lower()
            chosen = (selected_option_id or "").strip().lower()
            answer_meta["selected_option_id"] = chosen
            answer_meta["correct_option_id"] = correct
            return chosen == correct, correct, answer_meta

        if interaction == "reorder":
            expected = list(payload.get("correct_order") or [])
            given = list(reorder_order or [])
            answer_meta["reorder_order"] = given
            answer_meta["correct_order"] = expected
            return given == expected, "", answer_meta

        if interaction in ("free_text", "rewrite"):
            model = str(payload.get("model_answer") or "").strip().lower()
            given = (free_text_answer or "").strip().lower()
            answer_meta["free_text_answer"] = given
            answer_meta["model_answer"] = model
            if not model:
                return bool(given), model, answer_meta
            if given == model:
                return True, model, answer_meta
            # Accept answers that contain the model sentence (learner flexibility).
            return model in given or given in model, model, answer_meta

        return False, "", answer_meta

    async def submit_vocab_mcq(
        self,
        *,
        user_id: str,
        plan_code: str = "free",
        word_id: str,
        selected_option_id: Optional[str] = None,
        question_id: Optional[str] = None,
        free_text_answer: Optional[str] = None,
        reorder_order: Optional[List[int]] = None,
        pack_id: Optional[str] = None,
        time_taken: int = 0,
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        word = await self._get_vocab_word(word_id, pack_id=pack_id)
        if not word:
            return {"word": None, "is_correct": False, "progress": None}

        question_row = None
        if question_id and self.lexicon is not None:
            try:
                import uuid as _uuid

                question_row = await self.lexicon.get_question(
                    _uuid.UUID(str(question_id))
                )
            except (ValueError, TypeError):
                question_row = None
            if question_row and str(question_row.lexeme_id) != str(
                word.get("lexeme_id") or word_id
            ):
                question_row = None

        ai_feedback: Optional[Dict[str, Any]] = None
        ai_eval_failed = False
        ai_quota_exceeded = False
        upgrade_hint: Optional[str] = None

        if question_row is not None:
            if (
                _quiz_production_needs_ai(question_row)
                and (free_text_answer or "").strip()
            ):
                (
                    is_correct,
                    answer_meta,
                    ai_feedback,
                    ai_eval_failed,
                    ai_quota_exceeded,
                    upgrade_hint,
                ) = await self._grade_vocab_quiz_with_ai(
                    user_id=user_id,
                    plan_code=plan_code,
                    question=question_row,
                    word=word,
                    free_text_answer=free_text_answer or "",
                )
                q_payload = (
                    question_row.payload
                    if isinstance(question_row.payload, dict)
                    else {}
                )
                correct_option_id = str(q_payload.get("model_answer") or "")
            else:
                is_correct, correct_option_id, answer_meta = (
                    self._grade_vocab_quiz_answer(
                        question_row,
                        selected_option_id=selected_option_id,
                        free_text_answer=free_text_answer,
                        reorder_order=reorder_order,
                    )
                )
            q_type = question_row.type
            resolved_qid = str(question_row.id)
        else:
            correct_option_id = (word.get("mcq") or {}).get("correct_option_id") or ""
            chosen = (selected_option_id or "").strip().lower()
            is_correct = chosen == (correct_option_id or "").strip().lower()
            answer_meta = {
                "selected_option_id": chosen,
                "correct_option_id": correct_option_id,
            }
            q_type = word.get("question_type")
            resolved_qid = word.get("question_id")
        existing = await self.progress.get_one(
            user_id=user_id, content_id=word_id, content_type="vocabulary"
        )
        state = self._base_progress(
            user_id=user_id, word_id=word_id, now=now, existing=existing
        )
        attempts = int(state.get("total_attempts", 0)) + 1
        correct = int(state.get("correct_answers", 0)) + int(is_correct)
        state["total_attempts"] = attempts
        state["correct_answers"] = correct
        state["last_mcq_result"] = {
            **answer_meta,
            "is_correct": is_correct,
            "ai_feedback": ai_feedback,
            "created_at": now,
        }
        pack_id = (pack_id or word.get("pack_id") or "").strip()
        self._stamp_vocab_progress(state, word, pack_id=pack_id or None)
        state["last_studied"] = now
        state["updated_at"] = now
        pack_total = await self._pack_total_words(pack_id) if pack_id else 12
        mastery_delta = 0.0
        pack_stored = 0.0
        if is_correct:
            state["current_streak"] = int(state.get("current_streak", 0)) + 1
            state["best_streak"] = max(
                int(state.get("best_streak", 0)), int(state["current_streak"])
            )
            slot_key = resolved_qid or f"{q_type}:{answer_meta.get('mastery_slot', 0)}"
            credited_slots = {str(x) for x in (state.get("quiz_slots_credited") or [])}
            if slot_key not in credited_slots:
                credited_slots.add(slot_key)
                state["quiz_slots_credited"] = sorted(credited_slots)
                state["mcq_credited"] = True
                mastery_delta = delta_quiz_slot(pack_total, slots=QUIZ_MATRIX_SLOTS)
                pack_stored = await self._credit_pack_and_word(
                    user_id=user_id,
                    pack_id=pack_id,
                    state=state,
                    delta_pct=mastery_delta,
                    pack_total_words=pack_total,
                )
                state["mastery_step"] = min(
                    QUIZ_MATRIX_SLOTS,
                    len(credited_slots) + (1 if state.get("learn_credited") else 0),
                )
        else:
            state["mastery_step"] = 0
            state["mastery_level"] = "new"
            state["current_streak"] = 0
            if pack_id:
                await self._withdraw_word_cycle_points(
                    user_id=user_id, pack_id=pack_id, state=state
                )
            self._clear_vocab_cycle_flags(state)
            state["quiz_slots_credited"] = []
            state["failed_locked_until"] = self._tomorrow_start()
            state["spaced_repetition"] = {
                "ease_factor": 2.5,
                "interval": 1,
                "repetitions": 0,
                "last_reviewed": now,
                "next_review": state["failed_locked_until"],
            }

        if isinstance(answer_meta, dict):
            if resolved_qid:
                answer_meta["question_id"] = resolved_qid
            if q_type:
                answer_meta["question_type"] = q_type

        await self.vocab_attempts.insert(
            {
                "attempt_id": f"vatt_{secrets.token_urlsafe(10)}",
                "user_id": user_id,
                "word_id": word_id,
                "pack_id": word.get("pack_id"),
                "attempt_type": "mcq",
                "is_correct": is_correct,
                "answer": answer_meta,
                "ai_feedback": ai_feedback,
                "created_at": now,
            }
        )
        saved = await self.progress.upsert_vocab_state(
            user_id=user_id, word_id=word_id, update_doc=state
        )
        await self.stats.bump(
            user_id,
            content_type="vocabulary",
            is_correct=is_correct,
            time_taken=time_taken,
        )
        pack_pct = (
            pack_stored
            if pack_stored > 0
            else (
                await self._pack_mastery_after_credit(user_id, pack_id)
                if pack_id
                else 0.0
            )
        )
        await self._record_vocab_personalization(
            user_id=user_id,
            event_type="mcq",
            word_id=word_id,
            word=word,
            progress=saved,
            pack_mastery_pct=pack_pct,
            question_type=q_type,
            step="mcq",
            is_correct=is_correct,
            score=1.0 if is_correct else 0.0,
            time_taken=time_taken,
            answer_meta=answer_meta,
            ai_eval_meta=(
                {
                    "ai_status": (ai_feedback or {}).get("ai_status"),
                    "passed": (ai_feedback or {}).get("passed"),
                    "score": (ai_feedback or {}).get("score"),
                }
                if ai_feedback
                else None
            ),
            weakness_tags=[] if is_correct else [f"{q_type or 'quiz'}_wrong"],
            occurred_at=now,
        )
        return {
            "word_id": word_id,
            "is_correct": is_correct,
            "correct_option_id": correct_option_id,
            "locked_until": state.get("failed_locked_until"),
            "progress": saved,
            "mastery_delta_pct": round(mastery_delta, 4),
            "pack_mastery_pct": pack_pct,
            "ai_feedback": ai_feedback,
            "ai_eval_failed": ai_eval_failed,
            "ai_quota_exceeded": ai_quota_exceeded,
            "upgrade_hint": upgrade_hint,
        }

    async def get_vocab_stats(
        self,
        user_id: str,
        *,
        plan_code: str = "free",
        include_pack_payload: bool = False,
    ) -> Dict[str, Any]:
        stats = await self.stats.get_or_default(user_id)
        progress = await self.progress.list_for_user(
            user_id, content_type="vocabulary", limit=50_000
        )
        today = self._today_key()
        now = datetime.now(timezone.utc)
        due_today = 0
        learned_today = 0
        mastered = 0
        for item in progress:
            if item.get("mastery_level") == "mastered" or item.get("marked_known"):
                mastered += 1
            if self._is_seen_today(
                item.get("last_seen_date"), item.get("last_studied")
            ):
                learned_today += 1
            next_review = _as_utc_aware(
                (item.get("spaced_repetition") or {}).get("next_review")
            )
            if (
                next_review
                and next_review <= now
                and item.get("mastery_level") != "mastered"
            ):
                due_today += 1
        daily_counts = self._vocab_daily_counts(stats, progress)
        daily_mistakes = self._vocab_daily_mistakes(stats)
        if learned_today > 0:
            daily_counts[today] = max(daily_counts.get(today, 0), learned_today)
        packs = await self.list_vocab_packs(user_id=user_id, all_packs=True)
        profile = stats.get("vocab_profile") or {}
        weekly_insights = self._build_vocab_weekly_insights(
            daily_counts=daily_counts,
            progress=progress,
            packs=packs,
            profile=profile,
        )
        today_date = datetime.now(VN_TZ).date()
        daily_streak = compute_daily_streak(daily_counts, today=today_date)
        stats_payload = dict(stats)
        stats_payload["vocab_current_streak"] = daily_streak
        stats_payload["vocab_best_streak"] = max(
            int(stats.get("vocab_best_streak", 0)), daily_streak
        )
        payload = {
            "due_today": due_today,
            "learned_today": learned_today,
            "mastered_words": mastered,
            "total_progress_words": len(progress),
            "vocab_daily_counts": daily_counts,
            "vocab_daily_mistakes": daily_mistakes,
            "activity_today_key": today,
            "vocab_learned_by_category": await self._vocab_learned_by_category(
                progress
            ),
            "vocab_weekly_insights": weekly_insights,
            "stats": stats_payload,
            "recent_attempts": await self.vocab_attempts.list_recent(user_id, limit=10),
            "vocab_ai_quota": await self.vocab_ai_quota_snapshot(user_id, plan_code),
        }
        if include_pack_payload:
            payload["vocab_packs"] = packs
        return payload

    @staticmethod
    def _normalize_task_progress(
        raw: Optional[Dict[str, Any]],
    ) -> Dict[str, Dict[str, int]]:
        if not raw:
            return {}
        out: Dict[str, Dict[str, int]] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            out[str(key)] = {
                "completed": max(0, int(value.get("completed") or 0)),
                "total": max(1, int(value.get("total") or 1)),
            }
        return out

    @staticmethod
    def _task_progress_hash(task_progress: Dict[str, Dict[str, int]]) -> str:
        normalized = {k: task_progress[k] for k in sorted(task_progress)}
        return hashlib.sha256(
            json.dumps(normalized, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def _task_progress_has_completions(
        task_progress: Optional[Dict[str, Dict[str, int]]],
    ) -> bool:
        if not task_progress:
            return False
        return any(int(v.get("completed") or 0) > 0 for v in task_progress.values())

    def _stamp_coach_insight_fields(
        self,
        mission: Dict[str, Any],
        task_progress: Optional[Dict[str, Dict[str, int]]],
    ) -> Dict[str, Any]:
        mission = dict(mission)
        parsed = self._normalize_task_progress(task_progress)
        if self._task_progress_has_completions(parsed):
            tp_hash = self._task_progress_hash(parsed)
            mission["coach_insight_stale"] = (
                mission.get("task_progress_hash") != tp_hash
            )
        else:
            mission["coach_insight_stale"] = False
        mission.setdefault(
            "coach_insight_llm_refreshed",
            mission.get("source") in ("llm_generated", "llm_cached"),
        )
        mission.setdefault("coach_insight_refreshed_at", mission.get("generated_at"))
        return mission

    async def get_vocab_today_mission(
        self,
        user_id: str,
        *,
        plan_code: str = "free",
        locale: str = "vi",
        task_progress: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> Dict[str, Any]:
        mission = await self._generate_vocab_today_mission(
            user_id,
            plan_code=plan_code,
            locale=locale,
        )
        return self._stamp_coach_insight_fields(mission, task_progress)

    async def refresh_vocab_coach_insight(
        self,
        user_id: str,
        *,
        locale: str = "vi",
        task_progress: Optional[Dict[str, Any]] = None,
        plan_code: str = "free",
    ) -> Dict[str, Any]:
        parsed = self._normalize_task_progress(task_progress)
        mission_date = datetime.now(VN_TZ).date()
        stats_payload = await self.get_vocab_stats(
            user_id, plan_code=plan_code, include_pack_payload=True
        )
        stats = stats_payload.get("stats") or {}
        profile = stats.get("vocab_profile") or {}
        weekly = stats_payload.get("vocab_weekly_insights") or {}
        active_band = weekly.get("active_band_mastery") or {}
        weaknesses = await self._vocab_weaknesses_for_mission(
            user_id=user_id,
            stats_payload=stats_payload,
            locale=locale,
        )
        weaknesses = _localize_mission_weaknesses(weaknesses, locale)
        daily_counts = stats_payload.get("vocab_daily_counts") or {}
        today_date = datetime.now(VN_TZ).date()
        total_progress = int(stats_payload.get("total_progress_words") or 0)
        learned_today_count = int(stats_payload.get("learned_today") or 0)
        rhythm = classify_learner_rhythm(
            daily_counts=daily_counts,
            today=today_date,
            total_progress_words=total_progress,
            learned_today=learned_today_count,
        )
        streak_days = compute_daily_streak(daily_counts, today=today_date)
        context = {
            "mission_date": mission_date.isoformat(),
            "locale": locale,
            "task_progress": parsed,
            "user_profile": {
                "current_band": profile.get("current_band", 6.0),
                "target_band": profile.get("target_band", 7.0),
                "daily_goal": profile.get("daily_goal", 5),
                "plan_code": plan_code,
                "calibration_completed": bool(profile.get("calibration_completed")),
            },
            "stats": {
                "due_today": stats_payload.get("due_today", 0),
                "learned_today": stats_payload.get("learned_today", 0),
                "mastered_words": stats_payload.get("mastered_words", 0),
                "total_progress_words": total_progress,
            },
            "focus_signals": {
                "active_band": active_band.get("band"),
                "active_band_label": active_band.get("band_label"),
                "active_band_mastery_pct": active_band.get("mastery_pct"),
            },
            "weaknesses": weaknesses[:5],
            "learner_rhythm": rhythm,
            "activity": {"streak_days": streak_days},
        }

        coach_lines: List[str] = []
        llm_refreshed = False
        try:
            from aiforen.integrations.llm.factory import get_llm_provider
            from aiforen.integrations.llm.json_utils import (
                normalize_vocab_daily_mission_payload,
            )

            raw = await get_llm_provider().generate_vocab_daily_mission(context=context)
            normalized = normalize_vocab_daily_mission_payload(raw, context=context)
            coach_lines = list(normalized.get("coach_overview_lines") or [])[:3]
            llm_refreshed = bool(coach_lines)
        except Exception as exc:
            logger.warning("coach insight LLM refresh failed: {}", exc)

        if not coach_lines:
            coach_lines = build_coach_overview_lines(
                rhythm=rhythm,  # type: ignore[arg-type]
                locale=locale,
                streak=streak_days,
                active_days_14=sum(
                    1
                    for d in range(14)
                    if int(
                        daily_counts.get(
                            (today_date - timedelta(days=d)).isoformat(), 0
                        )
                        or 0
                    )
                    > 0
                ),
                total_progress_words=total_progress,
                learned_today=learned_today_count,
                due_today=int(stats_payload.get("due_today") or 0),
            )

        coach_lines = normalize_coach_overview_lines(
            coach_lines,
            rhythm=rhythm,  # type: ignore[arg-type]
            locale=locale,
            fallback_kwargs={
                "streak": streak_days,
                "total_progress_words": total_progress,
                "learned_today": learned_today_count,
                "due_today": int(stats_payload.get("due_today") or 0),
            },
        )
        refreshed_at = datetime.now(VN_TZ).isoformat()
        tp_hash = self._task_progress_hash(parsed) if parsed else None

        if self.personalization is not None:
            cached = await self.personalization.get_daily_mission(
                user_id=user_id,
                mission_date=mission_date,
                locale=locale,
            )
            if cached:
                output = dict(cached.get("output") or {})
                output["coach_overview_lines"] = coach_lines
                if tp_hash:
                    output["task_progress_hash"] = tp_hash
                await self.personalization.upsert_daily_mission(
                    user_id=user_id,
                    mission_date=mission_date,
                    locale=locale,
                    snapshot_hash=str(cached.get("snapshot_hash") or ""),
                    output=output,
                    status=str(cached.get("status") or "generated"),
                    model_provider=cached.get("model_provider"),
                    model_name=cached.get("model_name"),
                )

        return {
            "coach_overview_lines": coach_lines,
            "coach_insight_llm_refreshed": llm_refreshed,
            "coach_insight_stale": False,
            "coach_insight_refreshed_at": refreshed_at,
            "task_progress_hash": tp_hash,
        }

    async def _generate_vocab_today_mission(
        self,
        user_id: str,
        *,
        plan_code: str = "free",
        locale: str = "vi",
    ) -> Dict[str, Any]:
        mission_date = datetime.now(VN_TZ).date()
        stats_payload = await self.get_vocab_stats(
            user_id,
            plan_code=plan_code,
            include_pack_payload=True,
        )
        stats = stats_payload.get("stats") or {}
        profile = stats.get("vocab_profile") or {}
        packs = stats_payload.get("vocab_packs") or []

        recent_actions = await self._vocab_recent_actions_for_mission(
            user_id=user_id,
            stats_payload=stats_payload,
        )
        weaknesses = await self._vocab_weaknesses_for_mission(
            user_id=user_id,
            stats_payload=stats_payload,
            locale=locale,
        )
        weaknesses = _localize_mission_weaknesses(weaknesses, locale)
        weaknesses = rank_mission_weaknesses(weaknesses)
        due_today = int(stats_payload.get("due_today") or 0)
        mission_signals = build_mission_signals(
            weaknesses=weaknesses, due_today=due_today
        )
        weekly = stats_payload.get("vocab_weekly_insights") or {}
        active_band = weekly.get("active_band_mastery") or {}
        candidate_packs = self._vocab_candidate_packs_for_mission(
            packs=packs,
            weaknesses=weaknesses,
            active_band=active_band,
        )
        recent_mission_notes = await self._vocab_recent_mission_notes(
            user_id=user_id,
            before_date=mission_date,
            locale=locale,
        )
        daily_counts = stats_payload.get("vocab_daily_counts") or {}
        today_date = datetime.now(VN_TZ).date()
        total_progress = int(stats_payload.get("total_progress_words") or 0)
        learned_today_count = int(stats_payload.get("learned_today") or 0)
        learner_rhythm = classify_learner_rhythm(
            daily_counts=daily_counts,
            today=today_date,
            total_progress_words=total_progress,
            learned_today=learned_today_count,
        )
        learner_stage = classify_learner_stage(
            daily_counts=daily_counts,
            today=today_date,
            total_progress_words=total_progress,
            learned_today=learned_today_count,
        )
        streak_days = compute_daily_streak(daily_counts, today=today_date)
        calibration_insight = profile.get("calibration_insight") or {}
        if not isinstance(calibration_insight, dict):
            calibration_insight = {}
        context = {
            "mission_date": mission_date.isoformat(),
            "locale": locale,
            "user_profile": {
                "current_band": profile.get("current_band", 6.0),
                "target_band": profile.get("target_band", 7.0),
                "daily_goal": profile.get("daily_goal", 5),
                "plan_code": plan_code,
                "calibration_completed": bool(profile.get("calibration_completed")),
                "calibration_insight": calibration_insight,
            },
            "stats": {
                "due_today": stats_payload.get("due_today", 0),
                "learned_today": stats_payload.get("learned_today", 0),
                "mastered_words": stats_payload.get("mastered_words", 0),
                "total_progress_words": stats_payload.get("total_progress_words", 0),
            },
            "focus_signals": {
                "active_band": active_band.get("band"),
                "active_band_label": active_band.get("band_label"),
                "active_band_mastery_pct": active_band.get("mastery_pct"),
                "weak_stat_labels": [
                    w.get("stat_label") for w in weaknesses if w.get("stat_label")
                ],
                "weak_dimensions": [w.get("dimension") for w in weaknesses],
            },
            "recent_actions": recent_actions[:12],
            "weaknesses": weaknesses[:5],
            "mission_signals": mission_signals,
            "candidate_packs": candidate_packs[:8],
            "recent_mission_notes": recent_mission_notes,
            "learner_rhythm": learner_rhythm,
            "learner_stage": learner_stage,
            "activity": {
                "streak_days": streak_days,
                "active_days_7": sum(
                    1
                    for d in range(7)
                    if int(
                        daily_counts.get(
                            (today_date - timedelta(days=d)).isoformat(), 0
                        )
                        or 0
                    )
                    > 0
                ),
                "active_days_14": sum(
                    1
                    for d in range(14)
                    if int(
                        daily_counts.get(
                            (today_date - timedelta(days=d)).isoformat(), 0
                        )
                        or 0
                    )
                    > 0
                ),
                "active_days_30": sum(
                    1
                    for d in range(30)
                    if int(
                        daily_counts.get(
                            (today_date - timedelta(days=d)).isoformat(), 0
                        )
                        or 0
                    )
                    > 0
                ),
            },
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(context, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        if (
            bool(profile.get("calibration_completed"))
            and calibration_insight
            and total_progress < 8
            and not weaknesses
            and due_today == 0
        ):
            mission = self._finalize_vocab_mission_payload(
                mission=self._mission_payload_from_calibration_insight(
                    calibration_insight,
                    context=context,
                ),
                context=context,
                mission_date=mission_date,
                source=str(calibration_insight.get("source") or "calibration_insight"),
            )
            mission["calibration_completed"] = True
            await self._hydrate_mission_plan_word_ids(
                user_id=user_id,
                mission=mission,
                context=context,
            )
            return mission

        cached = None
        if self.personalization is not None:
            cached = await self.personalization.get_daily_mission(
                user_id=user_id,
                mission_date=mission_date,
                locale=locale,
            )
        if cached and (
            cached.get("snapshot_hash") == snapshot_hash
            or plan_code in ("free", "guest")
        ):
            cached_output = dict(cached.get("output") or {})
            source = (
                "llm_cached"
                if cached.get("status") == "generated"
                else "fallback_rules"
            )
            mission = self._finalize_vocab_mission_payload(
                mission={
                    "headline": cached_output.get("headline"),
                    "summary": cached_output.get("summary"),
                    "confidence": cached_output.get("confidence", 0.7),
                    "plan_blocks": cached_output.get("plan_blocks") or [],
                    "primary_cta": cached_output.get("primary_cta") or {},
                    "coach_overview_lines": cached_output.get("coach_overview_lines")
                    or [],
                },
                context=context,
                mission_date=mission_date,
                source=source,
            )
            mission.setdefault("generated_at", cached.get("generated_at"))
            if cached_output.get("task_progress_hash"):
                mission["task_progress_hash"] = cached_output["task_progress_hash"]
            await self._hydrate_mission_plan_word_ids(
                user_id=user_id,
                mission=mission,
                context=context,
            )
            return mission

        provider_name = "mock"
        model_name = None
        try:
            provider = get_llm_provider()
            provider_name = (
                provider.__class__.__name__.replace("LLMProvider", "").lower() or "llm"
            )
            raw = await provider.generate_vocab_daily_mission(context=context)
            mission = normalize_vocab_daily_mission_payload(raw, context=context)
            mission = self._finalize_vocab_mission_payload(
                mission=mission,
                context=context,
                mission_date=mission_date,
                source="llm_generated",
            )
            await self._hydrate_mission_plan_word_ids(
                user_id=user_id,
                mission=mission,
                context=context,
            )
            if self.personalization is not None:
                await self.personalization.upsert_daily_mission(
                    user_id=user_id,
                    mission_date=mission_date,
                    locale=locale,
                    snapshot_hash=snapshot_hash,
                    output=mission,
                    status="generated",
                    model_provider=provider_name,
                    model_name=model_name,
                    expires_at=datetime(
                        mission_date.year,
                        mission_date.month,
                        mission_date.day,
                        23,
                        59,
                        59,
                        tzinfo=VN_TZ,
                    ),
                )
            return mission
        except Exception as exc:
            logger.error(
                "vocab daily mission generation failed user={} err_type={} err={}",
                user_id,
                type(exc).__name__,
                exc,
            )
            mission = self._fallback_vocab_mission(
                context=context, mission_date=mission_date
            )
            await self._hydrate_mission_plan_word_ids(
                user_id=user_id,
                mission=mission,
                context=context,
            )
            if self.personalization is not None:
                await self.personalization.upsert_daily_mission(
                    user_id=user_id,
                    mission_date=mission_date,
                    locale=locale,
                    snapshot_hash=snapshot_hash,
                    output=mission,
                    status="fallback",
                    model_provider=provider_name,
                    model_name=model_name,
                    error_meta={"type": type(exc).__name__, "message": str(exc)[:500]},
                    expires_at=datetime(
                        mission_date.year,
                        mission_date.month,
                        mission_date.day,
                        23,
                        59,
                        59,
                        tzinfo=VN_TZ,
                    ),
                )
            return mission

    async def _vocab_recent_mission_notes(
        self,
        *,
        user_id: str,
        before_date: date,
        locale: str,
    ) -> List[Dict[str, Any]]:
        rows = await self.personalization.list_recent_daily_missions(
            user_id=user_id,
            before_date=before_date,
            locale=locale,
            limit=3,
        )
        notes: List[Dict[str, Any]] = []
        for row in rows:
            output = row.get("output") or {}
            if not isinstance(output, dict):
                continue
            notes.append(
                {
                    "mission_date": row.get("mission_date"),
                    "headline": output.get("headline"),
                    "summary": output.get("summary"),
                    "coach_overview_lines": (output.get("coach_overview_lines") or [])[
                        :3
                    ],
                    "plan_blocks": [
                        {
                            "type": block.get("type"),
                            "title": block.get("title"),
                            "target_count": block.get("target_count"),
                        }
                        for block in (output.get("plan_blocks") or [])[:3]
                        if isinstance(block, dict)
                    ],
                    "primary_cta": output.get("primary_cta") or {},
                }
            )
        return notes

    async def _vocab_recent_actions_for_mission(
        self,
        *,
        user_id: str,
        stats_payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if self.personalization is not None:
            actions = await self.personalization.recent_actions(user_id, limit=12)
            if actions:
                return await self._decorate_recent_vocab_actions(actions)
        legacy = []
        for attempt in stats_payload.get("recent_attempts") or []:
            legacy.append(
                {
                    "event_type": attempt.get("attempt_type") or "attempt",
                    "content_type": "vocabulary",
                    "word_id": attempt.get("word_id"),
                    "pack_id": attempt.get("pack_id"),
                    "question_type": attempt.get("question_type"),
                    "step": attempt.get("attempt_type"),
                    "is_correct": attempt.get("is_correct"),
                    "score": None,
                    "time_taken": 0,
                    "answer_meta": {},
                    "ai_eval_meta": {},
                    "weakness_tags": (
                        [] if attempt.get("is_correct") else ["meaning_mcq_wrong"]
                    ),
                    "occurred_at": (
                        attempt.get("created_at").isoformat()
                        if isinstance(attempt.get("created_at"), datetime)
                        else attempt.get("created_at")
                    ),
                }
            )
        return await self._decorate_recent_vocab_actions(legacy[:12])

    async def _decorate_recent_vocab_actions(
        self,
        actions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        decorated: List[Dict[str, Any]] = []
        cache: Dict[str, Dict[str, Any]] = {}
        for action in actions:
            word_id = action.get("word_id")
            word = None
            if word_id:
                if word_id not in cache:
                    cache[word_id] = (
                        await self._get_vocab_word(
                            word_id,
                            pack_id=action.get("pack_id"),
                        )
                        or {}
                    )
                word = cache[word_id]
            decorated.append(
                {
                    **action,
                    "word": (word or {}).get("word"),
                    "category": (word or {}).get("category"),
                    "stat_labels": (word or {}).get("stat_labels") or [],
                    "outcome": (
                        "correct"
                        if action.get("is_correct") is True
                        else (
                            "needs_repair"
                            if action.get("is_correct") is False
                            else "logged"
                        )
                    ),
                }
            )
        return decorated

    async def _vocab_weaknesses_for_mission(
        self,
        *,
        user_id: str,
        stats_payload: Dict[str, Any],
        locale: str = "vi",
    ) -> List[Dict[str, Any]]:
        vi = str(locale).lower().startswith("vi")
        if self.personalization is not None:
            weaknesses = await self.personalization.top_weaknesses(user_id, limit=5)
            if weaknesses:
                return weaknesses
        counts: Dict[str, int] = defaultdict(int)
        pack_by_dim: Dict[str, Optional[str]] = {}
        for attempt in stats_payload.get("recent_attempts") or []:
            if attempt.get("is_correct") is False:
                dim = (
                    "meaning_mcq_wrong"
                    if attempt.get("attempt_type") == "mcq"
                    else "translation_failed"
                )
                counts[dim] += 1
                pack_by_dim[dim] = attempt.get("pack_id")
        out = []
        for dim, count in sorted(counts.items(), key=lambda row: row[1], reverse=True)[
            :5
        ]:
            out.append(
                {
                    "dimension": dim,
                    "label": _MISSION_WEAKNESS_LABEL.get(dim, dim.replace("_", " ")),
                    "severity": float(count),
                    "evidence_count": count,
                    "last_seen_at": None,
                    "recommended_action_type": "repair_weakness",
                    "pack_id": pack_by_dim.get(dim),
                    "stat_label": None,
                    "band": None,
                    "evidence": {},
                    "suggested_repair": (
                        "Luyện weak area này trước khi thêm từ mới."
                        if vi
                        else "Practice this weak area before adding more new words."
                    ),
                }
            )
        return rank_mission_weaknesses(out[:5])

    def _mission_review_status(self, due_today: int) -> Dict[str, Any]:
        from aiforen.domain.vocab_mission_priority import review_status

        due = max(0, int(due_today or 0))
        severity = review_status(due)
        recommended = min(due, 8) if due > 0 else 0
        return {
            "due_count": due,
            "severity": severity,
            "recommended_count": recommended,
        }

    def _vocab_candidate_packs_for_mission(
        self,
        *,
        packs: List[Dict[str, Any]],
        weaknesses: List[Dict[str, Any]],
        active_band: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        weak_pack_ids = {w.get("pack_id") for w in weaknesses if w.get("pack_id")}
        active_band_value = active_band.get("band")

        def _score(pack: Dict[str, Any]) -> float:
            score = 0.0
            if pack.get("pack_id") in weak_pack_ids:
                score += 40
            if active_band_value is not None:
                try:
                    if (
                        abs(
                            float(pack.get("source_band_min") or 0)
                            - float(active_band_value)
                        )
                        <= 0.75
                    ):
                        score += 15
                except Exception:
                    pass
            progress_pct = float(pack.get("progress_percentage") or 0)
            if 0 < progress_pct < 100:
                score += 10
            if int(pack.get("learned_words") or 0) == 0:
                score += 4
            return score

        ranked = sorted(packs, key=_score, reverse=True)
        return [
            {
                "pack_id": pack.get("pack_id"),
                "title": pack.get("title") or pack.get("name") or pack.get("pack_id"),
                "category": pack.get("category"),
                "band_min": pack.get("source_band_min"),
                "band_max": pack.get("source_band_max"),
                "progress_percentage": pack.get("progress_percentage", 0),
                "learned_words": pack.get("learned_words", 0),
                "mastered_words": pack.get("mastered_words", 0),
                "total_words": pack.get("total_words", 0),
            }
            for pack in ranked
            if pack.get("pack_id")
        ]

    async def _mission_word_ids_for_pack(
        self,
        *,
        user_id: str,
        pack_id: str,
        target: int,
        mode: str,
        exclude: Set[str],
    ) -> List[str]:
        words = await self._list_vocab_words(pack_id=pack_id, limit=500)
        if not words:
            return []

        word_ids = [str(word["word_id"]) for word in words if word.get("word_id")]
        progress_items = await self._pack_progress_items(
            user_id=user_id,
            pack_id=pack_id,
            word_ids=word_ids,
        )
        now = datetime.now(timezone.utc)
        progress_by_id: Dict[str, Dict[str, Any]] = {}
        alias_to_progress: Dict[str, Dict[str, Any]] = {}
        progress_content_ids = [p["content_id"] for p in progress_items]
        all_ids = list({*progress_content_ids, *word_ids})
        id_map = (
            await self.lexicon.resolve_word_ids(all_ids)
            if self.lexicon is not None
            else {}
        )
        for progress in progress_items:
            content_id = progress["content_id"]
            alias_to_progress[content_id] = progress
            lex_id = id_map.get(content_id)
            if lex_id:
                alias_to_progress[str(lex_id)] = progress
        for word_id in word_ids:
            row = alias_to_progress.get(word_id)
            if row is None:
                lex_id = id_map.get(word_id)
                if lex_id:
                    row = alias_to_progress.get(str(lex_id))
            if row is not None:
                progress_by_id[word_id] = row

        due: List[str] = []
        new: List[str] = []
        for word_id in word_ids:
            if word_id in exclude:
                continue
            progress = progress_by_id.get(word_id)
            if progress and (
                progress.get("marked_known")
                or progress.get("mastery_level") == "mastered"
            ):
                continue
            if progress and self._is_seen_today(
                progress.get("last_seen_date"), progress.get("last_studied")
            ):
                continue
            locked_until = progress.get("failed_locked_until") if progress else None
            if locked_until and locked_until > now:
                continue
            next_review = _as_utc_aware(
                ((progress or {}).get("spaced_repetition") or {}).get("next_review")
            )
            if progress and next_review and next_review <= now:
                due.append(word_id)
            elif not progress:
                new.append(word_id)

        random.shuffle(due)
        random.shuffle(new)
        if mode == "review_due":
            pool = due or new
        else:
            pool = due + new
        cap = max(1, min(target, 20))
        return pool[:cap]

    async def _hydrate_mission_plan_word_ids(
        self,
        *,
        user_id: str,
        mission: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        recent_actions = context.get("recent_actions") or []
        primary_cta = mission.get("primary_cta") or {}
        default_pack_id = primary_cta.get("pack_id")
        reserved: Set[str] = set()
        practice_source: List[str] = []

        async def fill_block(block: Dict[str, Any]) -> None:
            nonlocal practice_source
            block_type = str(block.get("type") or "study_pack")
            existing = _dedupe_word_ids(
                [str(word_id) for word_id in (block.get("word_ids") or [])]
            )
            target = max(1, min(int(block.get("target_count") or 8), 20))
            if existing:
                block["word_ids"] = existing[:target]
                reserved.update(block["word_ids"])
                if block_type in {"repair_weakness", "study_pack"}:
                    practice_source.extend(block["word_ids"])
                return

            pack_id = block.get("pack_id") or default_pack_id
            selected: List[str] = []
            if block_type == "repair_weakness":
                for action in recent_actions:
                    if action.get("is_correct") is not False:
                        continue
                    word_id = str(action.get("word_id") or "").strip()
                    if not word_id or word_id in reserved:
                        continue
                    selected.append(word_id)
                    if len(selected) >= target:
                        break
                if not selected and pack_id:
                    selected = await self._mission_word_ids_for_pack(
                        user_id=user_id,
                        pack_id=str(pack_id),
                        target=target,
                        mode="study_pack",
                        exclude=reserved,
                    )
            elif block_type == "review_due" and pack_id:
                selected = await self._mission_word_ids_for_pack(
                    user_id=user_id,
                    pack_id=str(pack_id),
                    target=target,
                    mode="review_due",
                    exclude=reserved,
                )
            elif block_type == "production_practice":
                selected = _dedupe_word_ids(practice_source or list(reserved))[:target]
            elif pack_id:
                selected = await self._mission_word_ids_for_pack(
                    user_id=user_id,
                    pack_id=str(pack_id),
                    target=target,
                    mode="study_pack",
                    exclude=reserved,
                )

            block["word_ids"] = selected[:target]
            reserved.update(block["word_ids"])
            if block_type in {"repair_weakness", "study_pack"}:
                practice_source.extend(block["word_ids"])

        for block in mission.get("plan_blocks") or []:
            if isinstance(block, dict):
                await fill_block(block)

        for block in mission.get("supplementary_plan_blocks") or []:
            if isinstance(block, dict):
                await fill_block(block)

        if not primary_cta.get("word_ids"):
            action_type = str(primary_cta.get("action_type") or "")
            matched = next(
                (
                    block
                    for block in (mission.get("plan_blocks") or [])
                    if isinstance(block, dict) and block.get("type") == action_type
                ),
                None,
            )
            if matched and matched.get("word_ids"):
                primary_cta["word_ids"] = list(matched["word_ids"])
                mission["primary_cta"] = primary_cta

    def _finalize_vocab_mission_payload(
        self,
        *,
        mission: Dict[str, Any],
        context: Dict[str, Any],
        mission_date: date,
        source: str,
    ) -> Dict[str, Any]:
        focus = context.get("focus_signals") or {}
        signals = context.get("mission_signals") or {}
        stats = context.get("stats") or {}
        profile = context.get("user_profile") or {}
        locale = str(context.get("locale") or "vi")
        due_today = int(stats.get("due_today") or 0)
        daily_goal = int(profile.get("daily_goal") or 5)
        weaknesses = context.get("weaknesses") or []
        mission_type, primary_weakness = _pick_mission_type_for_display(
            weaknesses=weaknesses,
            due_today=due_today,
        )
        plan_blocks = reorder_plan_blocks(
            mission.get("plan_blocks") or [],
            mission_type=mission_type,  # type: ignore[arg-type]
        )
        plan_blocks = _normalize_plan_blocks_copy(
            plan_blocks,
            mission_type=mission_type,
            primary_weakness=primary_weakness,
            due_today=due_today,
            daily_goal=daily_goal,
            locale=locale,
        )
        headline, summary, cta_label, reason, expected_gain = _build_mission_copy(
            mission_type=mission_type,
            primary_weakness=primary_weakness,
            due_today=due_today,
            locale=locale,
            focus_band_label=focus.get("active_band_label"),
            focus_band_mastery_pct=focus.get("active_band_mastery_pct"),
        )
        primary_cta = dict(mission.get("primary_cta") or {})
        if mission_type == "repair_weakness":
            primary_cta["action_type"] = "repair_weakness"
            if primary_weakness:
                primary_cta.setdefault("pack_id", primary_weakness.get("pack_id"))
        elif mission_type == "review_recall":
            primary_cta["action_type"] = "review_due"
        primary_cta["label"] = cta_label
        if not primary_cta.get("task_steps"):
            primary_block = next(
                (
                    block
                    for block in plan_blocks
                    if block.get("type") == primary_cta.get("action_type")
                    or (
                        mission_type == "review_recall"
                        and block.get("type") == "review_due"
                    )
                ),
                plan_blocks[0] if plan_blocks else {},
            )
            primary_cta["task_steps"] = primary_block.get(
                "task_steps"
            ) or _mission_task_steps_for_block(
                str(
                    primary_block.get("type")
                    or primary_cta.get("action_type")
                    or "study_pack"
                )
            )
        rhythm = str(context.get("learner_rhythm") or "early")
        if rhythm not in {"new", "early", "intermittent", "consistent"}:
            rhythm = "early"
        activity = context.get("activity") or {}
        weak_label = (
            _weakness_display_label(primary_weakness) if primary_weakness else None
        )
        coach_fallback = {
            "streak": int(activity.get("streak_days") or 0),
            "active_days_14": int(activity.get("active_days_14") or 0),
            "total_progress_words": int(stats.get("total_progress_words") or 0),
            "learned_today": int(stats.get("learned_today") or 0),
            "due_today": due_today,
            "primary_weakness_label": weak_label,
        }
        coach_lines = normalize_coach_overview_lines(
            mission.get("coach_overview_lines"),
            rhythm=rhythm,  # type: ignore[arg-type]
            locale=locale,
            fallback_kwargs=coach_fallback,
        )
        candidate_packs = context.get("candidate_packs") or []
        default_pack_id = (
            candidate_packs[0].get("pack_id") if candidate_packs else None
        ) or focus.get("primary_pack_id")
        _backfill_mission_pack_ids(
            plan_blocks,
            primary_cta,
            default_pack_id=default_pack_id,
        )
        from aiforen.domain.vocab_mission_priority import is_hygiene_weakness

        learning_weaknesses = [
            w for w in weaknesses if isinstance(w, dict) and not is_hygiene_weakness(w)
        ]
        primary_focus = None
        if primary_weakness and not is_hygiene_weakness(primary_weakness):
            primary_focus = {
                "type": mission_type,
                "label": weak_label or "",
                "evidence_count": int(primary_weakness.get("evidence_count") or 0),
                "dimension": primary_weakness.get("dimension"),
            }
        return {
            "mission_date": mission_date.isoformat(),
            "focus_band": {
                "band": focus.get("active_band"),
                "label": focus.get("active_band_label"),
                "mastery_pct": focus.get("active_band_mastery_pct"),
            },
            "headline": headline,
            "summary": summary,
            "reason": reason,
            "expected_gain": expected_gain,
            "learner_rhythm": rhythm,
            "learner_stage": context.get("learner_stage") or "activation_under_3_days",
            "coach_overview_lines": coach_lines,
            "estimated_minutes": _estimate_mission_minutes(
                mission_type=mission_type,
                due_today=due_today,
                daily_goal=daily_goal,
                plan_blocks=plan_blocks,
            ),
            "confidence": mission.get("confidence", 0.6),
            "recent_actions": context.get("recent_actions") or [],
            "weaknesses": learning_weaknesses,
            "review_status": self._mission_review_status(due_today),
            "primary_focus": primary_focus,
            "plan_blocks": plan_blocks,
            "primary_cta": primary_cta,
            "mission_signals": signals,
            "source": source,
            "calibration_completed": bool(
                (context.get("user_profile") or {}).get("calibration_completed")
            ),
        }

    def _fallback_vocab_mission(
        self,
        *,
        context: Dict[str, Any],
        mission_date: date,
    ) -> Dict[str, Any]:
        stats = context.get("stats") or {}
        profile = context.get("user_profile") or {}
        weaknesses = context.get("weaknesses") or []
        candidate_packs = context.get("candidate_packs") or []
        focus = context.get("focus_signals") or {}
        signals = context.get("mission_signals") or {}
        due_today = int(stats.get("due_today") or 0)
        daily_goal = int(profile.get("daily_goal") or 5)
        primary_pack = candidate_packs[0] if candidate_packs else {}
        primary_pack_id = primary_pack.get("pack_id")
        locale = str(context.get("locale") or "en").lower()
        vi = locale.startswith("vi")
        mission_type, primary_weakness = _pick_mission_type_for_display(
            weaknesses=weaknesses,
            due_today=due_today,
        )
        blocks: List[Dict[str, Any]] = []

        if primary_weakness and mission_type == "repair_weakness":
            weak_label = primary_weakness.get("label", "weak area")
            blocks.append(
                {
                    "type": "repair_weakness",
                    "title": f"Sửa lỗi {weak_label}" if vi else f"Repair {weak_label}",
                    "description": primary_weakness.get("suggested_repair")
                    or (
                        "Làm lại các câu sai và xem nghĩa đúng."
                        if vi
                        else "Redo wrong items and check meanings."
                    ),
                    "target_count": min(
                        5, max(3, int(primary_weakness.get("evidence_count") or 3))
                    ),
                    "pack_id": primary_weakness.get("pack_id") or primary_pack_id,
                    "word_ids": [],
                }
            )

        if due_today > 0:
            blocks.append(
                {
                    "type": "review_due",
                    "title": (
                        "Review related words" if not vi else "Review từ liên quan"
                    ),
                    "description": (
                        f"Review {min(due_today, daily_goal)} từ cần ôn có liên quan trong session sửa lỗi."
                        if vi
                        else f"Review {min(due_today, daily_goal)} related review words in this repair session."
                    ),
                    "target_count": min(due_today, daily_goal),
                    "pack_id": primary_pack_id,
                    "word_ids": [],
                }
            )

        if (
            mission_type == "review_recall"
            and due_today > 0
            and not any(block.get("type") == "review_due" for block in blocks)
        ):
            blocks.insert(
                0,
                {
                    "type": "review_due",
                    "title": "Review due words" if not vi else "Review từ cần ôn",
                    "description": (
                        f"Review {min(due_today, daily_goal)} từ cần ôn trước khi học thêm."
                        if vi
                        else f"Review {min(due_today, daily_goal)} words before learning new ones."
                    ),
                    "target_count": min(due_today, daily_goal),
                    "pack_id": primary_pack_id,
                    "word_ids": [],
                },
            )

        blocks.append(
            {
                "type": "production_practice",
                "title": "Sentence practice" if vi else "Sentence practice",
                "description": (
                    "Viết 3 câu ngắn với các từ vừa sửa."
                    if vi
                    else "Write 3 short sentences with the words you just repaired."
                ),
                "target_count": 3,
                "pack_id": primary_pack_id,
                "word_ids": [],
            }
        )

        if mission_type == "study_pack":
            blocks.insert(
                0,
                {
                    "type": "study_pack",
                    "title": "Học một batch nhỏ" if vi else "Learn a controlled batch",
                    "description": (
                        "Thêm một nhóm từ từ pack phù hợp, tránh overload recall."
                        if vi
                        else "Add a small set of words from the best-fit pack and avoid overloading recall."
                    ),
                    "target_count": max(3, min(daily_goal, 8)),
                    "pack_id": primary_pack_id,
                    "word_ids": [],
                },
            )

        blocks = reorder_plan_blocks(blocks[:4], mission_type=mission_type)  # type: ignore[arg-type]

        if mission_type == "repair_weakness" and primary_weakness:
            weak_label = primary_weakness.get("label", "weak area")
            headline = (
                f"Hôm nay: sửa lỗi {weak_label}" if vi else f"Today: fix {weak_label}"
            )
            summary = (
                f"Bạn sai {weak_label} gần đây. Session này sẽ repair, review từ liên quan, rồi dùng từ trong câu."
                if vi
                else f"Recent {weak_label} mistakes detected. Repair, review related words, then practice in sentences."
            )
            if due_today > 0:
                summary = (
                    f"Bạn sai {weak_label} gần đây. {due_today} từ cũng đang due — mình đưa vào session sửa lỗi."
                    if vi
                    else f"Recent {weak_label} mistakes. {due_today} words are also due and included in this repair session."
                )
            primary_type = "repair_weakness"
            primary_pack = primary_weakness.get("pack_id") or primary_pack_id
            weak_label = _weakness_display_label(primary_weakness)
            cta_label = "Sửa lỗi MCQ" if "mcq" in weak_label.lower() else "Sửa lỗi"
            if not vi:
                cta_label = "Repair mistake"
        elif mission_type == "review_recall" and due_today > 0:
            headline = (
                f"Hôm nay: clear {due_today} due words"
                if vi
                else f"Today: clear {due_today} due words"
            )
            summary = (
                f"Bạn có {due_today} từ due. Review trước để bảo vệ recall."
                if vi
                else f"You have {due_today} due words. Review first to protect recall."
            )
            primary_type = "review_due"
            primary_pack = primary_pack_id
            cta_label = "Start review" if vi else "Start review"
        else:
            band_label = focus.get("active_band_label") or (
                "momentum từ vựng" if vi else "vocab momentum"
            )
            headline = (
                f"Hôm nay: tập trung {band_label}"
                if vi
                else f"Today: focus on {band_label}"
            )
            summary = (
                "Kế hoạch rule-based sẵn sàng khi AI planning chưa khả dụng."
                if vi
                else "A rule-based plan is ready while AI planning is unavailable."
            )
            primary_type = "study_pack"
            primary_pack = primary_pack_id
            cta_label = "Bắt đầu mission hôm nay" if vi else "Start today's mission"

        return self._finalize_vocab_mission_payload(
            mission={
                "headline": headline,
                "summary": summary,
                "confidence": 0.56,
                "plan_blocks": blocks,
                "primary_cta": {
                    "action_type": primary_type,
                    "label": cta_label,
                    "pack_id": primary_pack,
                    "word_ids": [],
                },
            },
            context={
                **context,
                "mission_signals": signals
                or build_mission_signals(weaknesses=weaknesses, due_today=due_today),
            },
            mission_date=mission_date,
            source="fallback_rules",
        )

    # ---------- sessions ----------

    async def start_session(
        self,
        *,
        user_id: str,
        session_type: str,
        content_type: str,
        category: Optional[str] = None,
        count: int = 10,
    ) -> Dict[str, Any]:
        if session_type == "review":
            content = await self.progress.due_for_review(user_id, content_type, count)
            content_ids = [c["content_id"] for c in content]
            if content_type == "grammar":
                items = []
                for cid in content_ids:
                    item = await self.grammar.get(cid)
                    if item:
                        items.append(item)
            else:
                items = []
                for cid in content_ids:
                    item = await self._get_vocab_word(cid)
                    if item:
                        items.append(item)
        else:
            kwargs = {"limit": count}
            if category:
                kwargs["category"] = category
            if content_type == "grammar":
                items = await self.grammar.list(**kwargs)
            else:
                items = await self._list_vocab_words(**kwargs)

        return {
            "session_id": f"sess_{secrets.token_urlsafe(8)}",
            "content": items,
            "session_type": session_type,
            "content_type": content_type,
            "category": category,
        }

    async def complete_session(
        self,
        *,
        user_id: str,
        session_id: str,
        results: List[Dict[str, Any]],
        total_time: int,
        structures_studied: int,
    ) -> Dict[str, Any]:
        for r in results:
            await self.progress.upsert_review(
                user_id=user_id,
                content_id=r["content_id"],
                content_type=r["content_type"],
                is_correct=bool(r.get("is_correct")),
                time_taken=int(r.get("time_taken", 0)),
                exercise_type=r.get("exercise_type", "session"),
            )
            await self.stats.bump(
                user_id,
                content_type=r["content_type"],
                is_correct=bool(r.get("is_correct")),
                time_taken=int(r.get("time_taken", 0)),
            )
        stats = await self.stats.get_or_default(user_id)
        return {
            "session_results": {
                "session_id": session_id,
                "structures_studied": structures_studied,
                "correct": sum(1 for r in results if r.get("is_correct")),
                "total_time": total_time,
                "completed_at": datetime.utcnow().isoformat(),
            },
            "updated_stats": stats,
        }
