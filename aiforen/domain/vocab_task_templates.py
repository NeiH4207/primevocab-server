"""Catalog of daily vocab session task templates for LLM selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

ALLOWED_TASK_STEPS = frozenset({"learn", "mcq", "translate", "topic"})
ALLOWED_WORD_SOURCES = frozenset({"wrong_answer", "due", "new", "pool", "calibration"})


@dataclass(frozen=True)
class VocabTaskTemplate:
    id: str
    label_vi: str
    label_en: str
    description_for_llm: str
    task_steps: tuple[str, ...]
    session_mode: str  # full | repair_mcq | review_due
    block_type: str
    min_band: Optional[float] = None
    max_band: Optional[float] = None
    prefers_sources: tuple[str, ...] = ("pool",)
    quiz_focus: Optional[str] = None

    def to_llm_catalog_entry(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label_en,
            "description": self.description_for_llm,
            "task_steps": list(self.task_steps),
            "block_type": self.block_type,
            "prefers_sources": list(self.prefers_sources),
            "quiz_focus": self.quiz_focus,
            "band_range": {
                "min": self.min_band,
                "max": self.max_band,
            },
        }


def _t(
    id: str,
    label_vi: str,
    label_en: str,
    description: str,
    *,
    task_steps: Sequence[str] = ("mcq",),
    session_mode: str = "full",
    block_type: str = "study_pack",
    min_band: Optional[float] = None,
    max_band: Optional[float] = None,
    prefers_sources: Sequence[str] = ("pool",),
    quiz_focus: Optional[str] = None,
) -> VocabTaskTemplate:
    steps = tuple(s for s in task_steps if s in ALLOWED_TASK_STEPS) or ("mcq",)
    sources = tuple(s for s in prefers_sources if s in ALLOWED_WORD_SOURCES) or (
        "pool",
    )
    return VocabTaskTemplate(
        id=id,
        label_vi=label_vi,
        label_en=label_en,
        description_for_llm=description,
        task_steps=steps,
        session_mode=session_mode,
        block_type=block_type,
        min_band=min_band,
        max_band=max_band,
        prefers_sources=sources,
        quiz_focus=quiz_focus,
    )


VOCAB_TASK_TEMPLATES: tuple[VocabTaskTemplate, ...] = (
    # Repair / mistakes
    _t(
        "repair_meaning_mcq",
        "Sửa nghĩa từ vừa sai",
        "Repair meaning MCQ",
        "Use when recent_actions show wrong meaning MCQs. One word per task; MCQ only, no learn gate.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "due", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "repair_collocation_mcq",
        "Sửa collocation vừa sai",
        "Repair collocation MCQ",
        "Use when weakness dimension is collocation or learner confuses word partners.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="collocation",
    ),
    _t(
        "repair_rewrite_mcq",
        "Sửa câu / rewrite",
        "Repair rewrite MCQ",
        "Use when recent mistakes involve sentence rewrite or free-text quiz steps.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="rewrite",
    ),
    _t(
        "repair_confusion_pair",
        "Phân biệt từ dễ nhầm",
        "Confusion-pair repair",
        "Use when evidence suggests confusion between near synonyms; prioritize wrong_answer words.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "repair_high_evidence",
        "Sửa weak spot mạnh",
        "High-evidence repair",
        "Use when top weakness evidence_count >= 3; focus wrong answers from that dimension.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "due"),
        quiz_focus="meaning",
    ),
    # Review due
    _t(
        "review_due_meaning",
        "Ôn từ due — nghĩa",
        "Due review meaning",
        "Use when due_today is medium/high and mission is recall-focused; MCQ meaning only.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "review_due_light",
        "Ôn nhẹ từ due",
        "Light due review",
        "Use when due_today is small (1–4); quick MCQ refresh before new words.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due",),
        quiz_focus="meaning",
    ),
    _t(
        "review_due_heavy",
        "Ôn mạnh backlog due",
        "Heavy due backlog",
        "Use when due_today >= daily_goal; prioritize due words only until backlog eases.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due",),
        quiz_focus="meaning",
    ),
    _t(
        "review_spaced_mix",
        "Ôn spaced + từ mới",
        "Spaced review mix",
        "Use when due exists but learner is consistent; mix due first then one new word.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due", "new", "pool"),
        quiz_focus="meaning",
    ),
    # New words / study
    _t(
        "study_learn_then_quiz",
        "Học từ mới + quiz",
        "Learn then quiz",
        "Default for new words at target band: show learn card then one MCQ per word.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "study_mcq_only_new",
        "Quiz từ mới (không learn)",
        "New words MCQ only",
        "Use for returning learners with calibration done; faster batch of new words.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "study_band_push",
        "Đẩy band — từ mới",
        "Band push new words",
        "Use when active_band mastery is high and target_band is higher; new words slightly above comfort.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        min_band=5.0,
        prefers_sources=("new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "study_foundation_band",
        "Củng cố band nền",
        "Foundation band study",
        "Use for lower bands (<5.5) or new_user; shorter learn+meaning MCQ.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        max_band=5.5,
        prefers_sources=("new", "pool", "calibration"),
        quiz_focus="meaning",
    ),
    _t(
        "study_gre_precision",
        "GRE — nghĩa chính xác",
        "GRE precision meaning",
        "Use for GRE/advanced packs; meaning MCQ with precise definitions.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        min_band=6.0,
        prefers_sources=("new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "study_ielts_topic",
        "IELTS — từ theo chủ đề",
        "IELTS topic vocabulary",
        "Use when pack category is thematic IELTS; learn+quiz on topic collocations.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("new", "pool"),
        quiz_focus="collocation",
    ),
    # Collocation / production skills
    _t(
        "collocation_mcq_batch",
        "Luyện collocation",
        "Collocation MCQ batch",
        "Use when weakness is collocation but not only wrong-answer repair; pool words at band.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("wrong_answer", "pool", "new"),
        quiz_focus="collocation",
    ),
    _t(
        "translate_prompt_mcq",
        "Dịch / translate step",
        "Translate-style MCQ",
        "Use when translate interaction is available; one word, translate-focused quiz.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("pool", "new"),
        quiz_focus="translate",
    ),
    _t(
        "production_sentence_topic",
        "Viết câu với từ",
        "Production sentence",
        "Use after repair words or for production_practice mission type; topic/free-text step.",
        task_steps=("topic",),
        session_mode="full",
        block_type="production_practice",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="production",
    ),
    _t(
        "production_short_batch",
        "Viết 3 câu ngắn",
        "Short production batch",
        "Legacy production practice; sentence-level, same template for each practiced word.",
        task_steps=("topic",),
        session_mode="full",
        block_type="production_practice",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="production",
    ),
    # Rhythm / learner stage
    _t(
        "consistent_daily_mcq",
        "MCQ đều mỗi ngày",
        "Consistent daily MCQ",
        "Use when learner_rhythm is consistent; balanced due+new, MCQ-only for speed.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("due", "new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "intermittent_catchup",
        "Bắt kịp sau nghỉ",
        "Intermittent catch-up",
        "Use when learner_rhythm is intermittent; due first, fewer new words.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due", "wrong_answer"),
        quiz_focus="meaning",
    ),
    _t(
        "early_activation_learn",
        "Kích hoạt — learn+quiz",
        "Early activation learn",
        "Use for early/new stage with low total_progress_words; learn+quiz on easy new words.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("new", "calibration", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "calibration_followup",
        "Sau calibration",
        "Post-calibration batch",
        "Use when calibration just completed and total_progress_words < 8.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("calibration", "new", "pool"),
        quiz_focus="meaning",
    ),
    # Mixed session templates
    _t(
        "mixed_repair_then_new",
        "Sửa lỗi rồi học mới",
        "Repair then new",
        "Use when both wrong answers and room for new words; wrong_answer words first, then new from pool.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "mixed_review_new_balance",
        "Cân bằng due + mới",
        "Due + new balance",
        "Use for study_pack mission with moderate due; alternate due and new sources.",
        task_steps=("learn", "mcq"),
        session_mode="full",
        block_type="study_pack",
        prefers_sources=("due", "new", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "exam_week_intensive",
        "Tuần thi — intensive MCQ",
        "Exam week intensive",
        "Use when target_band is soon and due+weakness both present; MCQ-only intensive.",
        task_steps=("mcq",),
        session_mode="full",
        block_type="study_pack",
        min_band=5.5,
        prefers_sources=("wrong_answer", "due", "pool"),
        quiz_focus="meaning",
    ),
    _t(
        "recall_strength_mcq",
        "Củng cố recall",
        "Recall strength MCQ",
        "Use when primary mission is review_recall; all words from due/review pool.",
        task_steps=("mcq",),
        session_mode="review_due",
        block_type="review_due",
        prefers_sources=("due",),
        quiz_focus="meaning",
    ),
    _t(
        "weakness_dimension_drill",
        "Drill theo weakness",
        "Weakness dimension drill",
        "Use when a single weakness dimension dominates; words from wrong_answer + pack pool.",
        task_steps=("mcq",),
        session_mode="repair_mcq",
        block_type="repair_weakness",
        prefers_sources=("wrong_answer", "pool"),
        quiz_focus="meaning",
    ),
)

_TEMPLATES_BY_ID: Dict[str, VocabTaskTemplate] = {t.id: t for t in VOCAB_TASK_TEMPLATES}


def all_vocab_task_templates() -> tuple[VocabTaskTemplate, ...]:
    return VOCAB_TASK_TEMPLATES


def get_vocab_task_template(template_id: str) -> Optional[VocabTaskTemplate]:
    return _TEMPLATES_BY_ID.get(str(template_id or "").strip())


def default_template_for_mission_type(mission_type: str) -> VocabTaskTemplate:
    mapping = {
        "repair_weakness": "repair_meaning_mcq",
        "review_recall": "review_due_meaning",
        "study_pack": "study_learn_then_quiz",
    }
    tid = mapping.get(str(mission_type or "").strip(), "study_learn_then_quiz")
    return _TEMPLATES_BY_ID.get(tid) or VOCAB_TASK_TEMPLATES[0]


def build_template_catalog_for_context(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return LLM-facing catalog entries, optionally filtered by learner band."""
    profile = context.get("user_profile") or {}
    try:
        band = float(profile.get("current_band") or 6.0)
    except (TypeError, ValueError):
        band = 6.0

    out: List[Dict[str, Any]] = []
    for template in VOCAB_TASK_TEMPLATES:
        if template.min_band is not None and band < template.min_band:
            continue
        if template.max_band is not None and band > template.max_band:
            continue
        out.append(template.to_llm_catalog_entry())
    return out


def compute_word_count_range(context: Dict[str, Any]) -> Dict[str, int]:
    """Dynamic min/max word-task count for the daily session."""
    stats = context.get("stats") or {}
    profile = context.get("user_profile") or {}
    due_today = int(stats.get("due_today") or 0)
    daily_goal = max(3, int(profile.get("daily_goal") or 5))
    stage = str(context.get("learner_stage") or "activation_under_3_days")
    rhythm = str(context.get("learner_rhythm") or "early")

    min_count = max(3, min(daily_goal, 5))
    max_count = max(min_count, min(20, daily_goal + 3))

    if due_today >= daily_goal:
        min_count = max(min_count, min(due_today, daily_goal))
        max_count = max(max_count, min(due_today + 2, 12))
    elif due_today > 0:
        max_count = max(max_count, min(due_today + daily_goal, 12))

    if stage == "new_user":
        min_count, max_count = 3, min(6, max_count)
    elif rhythm == "intermittent":
        max_count = min(max_count, 8)

    return {"min": min_count, "max": max_count}


def word_task_mission_enabled() -> bool:
    import os

    return os.getenv("VOCAB_WORD_TASK_MISSION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
