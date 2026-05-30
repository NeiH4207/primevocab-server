"""Deterministic local LLM stub.

Streams realistic-looking IELTS-style feedback so the writing flow is
fully demoable without external API keys.  Uses the same wire contract
as the future Anthropic/Gemini providers.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any, AsyncIterator, Dict, List

from .base import EvaluationStreamEvent, LLMProvider
from .json_utils import normalize_vocab_eval_payload

_CRITERIA_ORDER = [
    ("task_achievement", "Task Achievement"),
    ("coherence_cohesion", "Coherence & Cohesion"),
    ("lexical_resource", "Lexical Resource"),
    ("grammar_accuracy", "Grammatical Range & Accuracy"),
]


def _seeded_random(answer: str) -> random.Random:
    seed_int = int(hashlib.sha256(answer.encode()).hexdigest()[:16], 16)
    return random.Random(seed_int)


def _bucket_score(words: int, rng: random.Random, baseline: float) -> float:
    if words < 100:
        score = baseline - 1.4
    elif words < 200:
        score = baseline - 0.4
    elif words < 280:
        score = baseline + 0.2
    else:
        score = baseline + 0.6
    score += rng.uniform(-0.5, 0.5)
    return max(3.0, min(9.0, round(score * 2) / 2))


def _criterion_feedback(name: str, score: float) -> str:
    if score >= 7.5:
        return (
            f"Strong {name.lower()}: ideas are clearly developed and "
            "well supported. Tighten paraphrasing and add one more concrete "
            "example to push to band 8."
        )
    if score >= 6.5:
        return (
            f"Solid {name.lower()}, but several arguments rely on generalisations. "
            "Replace vague quantifiers with specific data, and link paragraphs "
            "with cohesive devices."
        )
    if score >= 5.5:
        return (
            f"{name} is uneven. Sentence-level grammar slips and missing "
            "topic sentences pull the response down. Rebuild around a clear "
            "thesis statement and check verb tenses."
        )
    return (
        f"{name} needs significant work. The response struggles to address "
        "the prompt directly; outline the main argument before drafting."
    )


def _build_assessment(answer: str) -> Dict[str, Any]:
    rng = _seeded_random(answer)
    words = len([w for w in answer.split() if w])
    scores: Dict[str, float] = {}
    feedback: Dict[str, str] = {}
    for key, label in _CRITERIA_ORDER:
        baseline = {
            "task_achievement": 6.5,
            "coherence_cohesion": 6.4,
            "lexical_resource": 6.6,
            "grammar_accuracy": 6.3,
        }[key]
        score = _bucket_score(words, rng, baseline)
        scores[key] = score
        feedback[key] = _criterion_feedback(label, score)
    overall = round(sum(scores.values()) / 4 * 2) / 2
    return {
        "task_achievement": {
            "score": scores["task_achievement"],
            "feedback": feedback["task_achievement"],
        },
        "coherence_cohesion": {
            "score": scores["coherence_cohesion"],
            "feedback": feedback["coherence_cohesion"],
        },
        "lexical_resource": {
            "score": scores["lexical_resource"],
            "feedback": feedback["lexical_resource"],
        },
        "grammar_accuracy": {
            "score": scores["grammar_accuracy"],
            "feedback": feedback["grammar_accuracy"],
        },
        "scores": {**scores, "overall_score": overall},
        "general_comments": (
            f"Overall band {overall}. The response is {'on-task' if words >= 200 else 'under-developed'} "
            f"with {words} words. Work on linking ideas across paragraphs and "
            "introducing more sophisticated lexis."
        ),
        "improvement_suggestions": (
            "1. Replace simple verbs (e.g., 'shows', 'gets') with academic synonyms.\n"
            "2. Add one specific example or piece of data per body paragraph.\n"
            "3. End with a stance, not just a summary.\n"
            "4. Vary sentence openings — avoid starting three sentences in a row "
            "with 'The' or 'It is'."
        ),
        "improvement_explanation": (
            "These changes target both Lexical Resource and Coherence & Cohesion, "
            "which have the largest band-impact for your current level."
        ),
        "next_level_sample": (
            "It is often argued that working remotely undermines team cohesion. "
            "While this view has merit, the broader evidence suggests that "
            "well-designed hybrid arrangements can actually strengthen "
            "collaboration. For instance, …"
        ),
    }


class MockLLMProvider(LLMProvider):
    async def generate_vocab_calibration_review(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        rule = context.get("rule_estimate") or {}
        cefr = str(rule.get("cefr_level") or "B1")
        locale = str(context.get("locale") or "vi").lower()
        vi = locale.startswith("vi")
        known = int(rule.get("known_count") or 0)
        total = int(rule.get("answer_count") or 12)
        return {
            "headline": (
                f"Trình độ từ vựng khoảng {cefr}" if vi else f"Vocabulary around {cefr}"
            ),
            "summary": (
                f"Bạn nhận biết {known}/{total} từ trong bài check. Ưu tiên pack {cefr} và luyện dùng từ trong câu ngắn."
                if vi
                else f"You recognized {known}/{total} words. Start a {cefr} pack and practice short sentences."
            ),
            "cefr_level": cefr,
            "confidence": float(rule.get("confidence") or 0.65),
            "strengths": [
                (
                    "Bạn phân biệt được từ đã gặp và từ dùng được."
                    if vi
                    else "You separate recognition from production."
                )
            ],
            "weak_spots": [
                (
                    "Một số từ mức Seen/New cần recall trước khi học thêm."
                    if vi
                    else "Some Seen/New words need recall first."
                )
            ],
            "recommended_plan": [
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
            ],
        }

    async def generate_vocab_daily_mission(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        profile = context.get("user_profile") or {}
        focus_signals = context.get("focus_signals") or {}
        mission_signals = context.get("mission_signals") or {}
        candidate_packs = context.get("candidate_packs") or []
        weaknesses = context.get("weaknesses") or []
        due_today = int((context.get("stats") or {}).get("due_today") or 0)
        daily_goal = int(profile.get("daily_goal") or 5)
        primary_pack = candidate_packs[0] if candidate_packs else {}
        primary_pack_id = primary_pack.get("pack_id")
        mission_type = str(
            mission_signals.get("primary_mission_type") or "repair_weakness"
        )
        primary_weakness = next(
            (
                w
                for w in weaknesses
                if w.get("dimension")
                == mission_signals.get("primary_weakness_dimension")
            ),
            None,
        )
        top_weakness = primary_weakness or next(
            (
                w
                for w in weaknesses
                if w.get("dimension") not in {"stale_review_due", "review_due"}
            ),
            None,
        )
        focus_band = (
            focus_signals.get("active_band_label")
            or focus_signals.get("active_band")
            or profile.get("current_band")
            or "your current band"
        )
        locale = str(context.get("locale") or "vi").lower()
        vi = locale.startswith("vi")

        blocks: List[Dict[str, Any]] = []
        if top_weakness and mission_type == "repair_weakness":
            weak_label = top_weakness.get("label", "weak area")
            blocks.append(
                {
                    "type": "repair_weakness",
                    "title": f"Sửa lỗi {weak_label}" if vi else f"Repair {weak_label}",
                    "description": top_weakness.get("suggested_repair")
                    or (
                        "Làm lại các câu sai và xem nghĩa đúng."
                        if vi
                        else "Redo wrong items and check meanings."
                    ),
                    "target_count": min(
                        5, max(3, int(top_weakness.get("evidence_count") or 3))
                    ),
                    "pack_id": top_weakness.get("pack_id") or primary_pack_id,
                    "word_ids": [],
                }
            )
        if due_today > 0:
            blocks.append(
                {
                    "type": "review_due",
                    "title": "Review từ liên quan" if vi else "Review related words",
                    "description": (
                        f"Review {min(due_today, daily_goal)} từ due có liên quan trong session sửa lỗi."
                        if vi
                        else f"Review {min(due_today, daily_goal)} related due words in this repair session."
                    ),
                    "target_count": min(due_today, daily_goal),
                    "pack_id": primary_pack_id,
                    "word_ids": [],
                }
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
                    "title": (
                        "Học thêm một batch từ mới"
                        if vi
                        else "Add a small set of new words"
                    ),
                    "description": (
                        "Học một nhóm từ vừa phải, rồi dùng ngay trong câu."
                        if vi
                        else "Learn a controlled batch, then immediately use the words in sentences."
                    ),
                    "target_count": max(3, min(daily_goal, 8)),
                    "pack_id": primary_pack_id,
                    "word_ids": [],
                },
            )

        from aiforen.domain.vocab_mission_priority import reorder_plan_blocks

        blocks = reorder_plan_blocks(blocks[:4], mission_type=mission_type)  # type: ignore[arg-type]

        if mission_type == "repair_weakness" and top_weakness:
            weak_label = top_weakness.get("label", "weak area")
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
            cta_type = "repair_weakness"
            cta_label = "Sửa weak spot" if vi else "Repair weak spot"
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
            cta_type = "review_due"
            cta_label = "Start review" if vi else "Start review"
        else:
            headline = (
                f"Hôm nay: ổn định từ vựng Band {focus_band}"
                if vi
                else f"Today: stabilize band {focus_band} vocabulary"
            )
            summary = (
                "Mission cân bằng repair weak area và luyện production."
                if vi
                else "Your mission balances weak-area repair and production practice."
            )
            cta_type = "study_pack"
            cta_label = "Bắt đầu mission" if vi else "Start mission"

        from aiforen.domain.vocab_learner_rhythm import build_coach_overview_lines

        rhythm = str(context.get("learner_rhythm") or "early")
        if rhythm not in {"new", "early", "intermittent", "consistent"}:
            rhythm = "early"
        activity = context.get("activity") or {}
        coach_lines = build_coach_overview_lines(
            rhythm=rhythm,  # type: ignore[arg-type]
            locale=locale,
            streak=int(activity.get("streak_days") or 0),
            active_days_14=int(activity.get("active_days_14") or 0),
            total_progress_words=int(
                (context.get("stats") or {}).get("total_progress_words") or 0
            ),
            learned_today=int((context.get("stats") or {}).get("learned_today") or 0),
            due_today=due_today,
            primary_weakness_label=top_weakness.get("label") if top_weakness else None,
        )

        return {
            "headline": headline,
            "summary": summary,
            "confidence": 0.74,
            "plan_blocks": blocks,
            "coach_overview_lines": coach_lines,
            "primary_cta": {
                "action_type": cta_type,
                "label": cta_label,
                "pack_id": (
                    top_weakness.get("pack_id") if top_weakness else primary_pack_id
                )
                or primary_pack_id,
                "word_ids": [],
            },
        }

    async def evaluate_vocab_sentence(
        self,
        *,
        word: str,
        translate_prompt: str,
        topic_prompt: str,
        translate_sentence: str,
        topic_sentence: str,
        current_band: float,
        target_band: float,
        weakness_context: list[str] | None = None,
    ) -> Dict[str, Any]:
        _ = weakness_context

        def _per(sentence: str, prompt: str) -> Dict[str, Any]:
            s = sentence.strip()
            if not s:
                return {
                    "status": "fail",
                    "is_grammatically_ok": False,
                    "answers_prompt": False,
                    "uses_target_word": False,
                    "corrected_sentence": "",
                    "recommendation": "Write an English sentence that answers the prompt.",
                }
            contains_word = word.lower() in s.lower()
            answers = len(s.split()) >= 4 and bool(prompt.strip())
            if not contains_word or not answers:
                status = "fail"
            elif len(s.split()) >= 6:
                status = "ok"
            else:
                status = "needs_fix"
            return {
                "status": status,
                "is_grammatically_ok": status == "ok",
                "answers_prompt": answers,
                "uses_target_word": contains_word,
                "corrected_sentence": s,
                "recommendation": (
                    f"Use '{word}' in a complete IELTS-style sentence "
                    f"that answers the prompt '{prompt}'."
                ),
            }

        translate = _per(translate_sentence, translate_prompt)
        topic = _per(topic_sentence, topic_prompt)

        return normalize_vocab_eval_payload(
            {
                "translate": translate,
                "topic": topic,
                "band_style_tip": (
                    f"For band {target_band:.1f}, make sentences more specific "
                    "and avoid vague nouns like 'thing' or 'people'."
                ),
            },
            translate_sentence=translate_sentence,
            topic_sentence=topic_sentence,
        )

    async def evaluate_writing(
        self, *, task: Dict[str, Any], answer: str
    ) -> AsyncIterator[EvaluationStreamEvent]:
        steps: List[tuple[str, str]] = [
            ("task_achievement", "Reading the question…"),
            ("coherence_cohesion", "Mapping paragraph flow…"),
            ("lexical_resource", "Scoring word choice…"),
            ("grammar_accuracy", "Checking grammar accuracy…"),
        ]

        assessment = _build_assessment(answer)

        for step, message in steps:
            yield EvaluationStreamEvent(status="processing", step=step, message=message)
            await asyncio.sleep(0.4)
            yield EvaluationStreamEvent(
                status="completed",
                step=step,
                content=assessment[step],
            )

        yield EvaluationStreamEvent(
            status="processing", step="general_comments", message="Drafting summary…"
        )
        await asyncio.sleep(0.3)
        yield EvaluationStreamEvent(
            status="completed",
            step="general_comments",
            content=assessment["general_comments"],
        )

        yield EvaluationStreamEvent(
            status="processing",
            step="improvement_suggestions",
            message="Compiling suggestions…",
        )
        await asyncio.sleep(0.3)
        yield EvaluationStreamEvent(
            status="completed",
            step="improvement_suggestions",
            content=assessment["improvement_suggestions"],
        )
        yield EvaluationStreamEvent(
            status="completed",
            step="improvement_explanation",
            content=assessment["improvement_explanation"],
        )

        yield EvaluationStreamEvent(
            status="processing",
            step="next_level_sample",
            message="Drafting band-up sample…",
        )
        await asyncio.sleep(0.3)
        yield EvaluationStreamEvent(
            status="completed",
            step="next_level_sample",
            content=assessment["next_level_sample"],
        )

        yield EvaluationStreamEvent(
            status="completed",
            step="final",
            data=assessment,
        )
