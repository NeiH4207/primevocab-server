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
from .json_utils import (
    normalize_coaching_notes_payload,
    normalize_reading_explain_payload,
    normalize_reading_questions_payload,
    normalize_vocab_eval_payload,
)

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

    async def explain_reading_phrase(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        phrase = str(context.get("phrase") or "").strip()
        sentence = str(context.get("sentence") or "").strip()
        level = str(context.get("level") or "B1")
        head = phrase.split()[0] if phrase.split() else phrase
        return normalize_reading_explain_payload(
            {
                "explanation": (
                    f'"{phrase}" works as one idea here. In the sentence "{sentence}", '
                    f"it links the key word to the writer's point rather than standing alone. "
                    f"At {level}, try saying it in your own words first."
                ),
                "paraphrase": f"in other words: {phrase.lower()}",
                "vocab_notes": [f"Focus word: {head}"] if head else [],
            },
            phrase=phrase,
            sentence=sentence,
            level=level,
        )

    async def generate_reading_questions(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        count = int(context.get("count") or 4)
        words: List[str] = []
        for key in ("looked_up_words", "bolded_words", "difficult_words"):
            for item in context.get(key) or []:
                token = item.get("word") if isinstance(item, dict) else item
                token = str(token or "").strip()
                if token and token not in words:
                    words.append(token)
        questions: List[Dict[str, Any]] = []
        for word in words[:count]:
            questions.append(
                {
                    "type": "vocabulary",
                    "prompt": f'Which option best matches how "{word}" is used in the passage?',
                    "options": [
                        f"the intended meaning of {word} in context",
                        f"an unrelated meaning of {word}",
                        f"the opposite of {word}",
                    ],
                    "correct_option": f"the intended meaning of {word} in context",
                    "explanation": (
                        f'Re-read the sentence around "{word}" and match the meaning to context.'
                    ),
                    "source_word": word,
                }
            )
        while len(questions) < count:
            questions.append(
                {
                    "type": "comprehension",
                    "prompt": "What is the main idea of the passage?",
                    "options": [
                        "It explains the topic and gives supporting detail",
                        "It tells a personal story with no facts",
                        "It lists unrelated opinions",
                    ],
                    "correct_option": "It explains the topic and gives supporting detail",
                    "explanation": "The passage develops one topic with concrete supporting detail.",
                    "source_word": "",
                }
            )
        return normalize_reading_questions_payload(
            {"questions": questions},
            count=count,
            fallback_questions=context.get("fallback_questions"),
        )

    async def generate_coaching_notes(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        level = str(context.get("level") or "B1")
        looked_up = [str(w) for w in (context.get("looked_up_words") or [])][:8]
        reading_correct = int(context.get("reading_correct") or 0)
        reading_total = int(context.get("reading_total") or 0)
        notes = [
            f"Anchor level: {level}. Keep most new words near this band until recall is stable.",
            (
                f"Revisit looked-up words tomorrow: {', '.join(looked_up[:6])}."
                if looked_up
                else "No lookup-heavy word today; keep the normal daily mix."
            ),
            (
                f"Reading: {reading_correct}/{reading_total} correct — "
                + (
                    "add easier context questions next time."
                    if reading_total and reading_correct / max(1, reading_total) < 0.7
                    else "ready for a slightly denser passage."
                )
            ),
        ]
        return normalize_coaching_notes_payload(
            {
                "headline": f"Day {context.get('day_number') or 1}: steady {level} progress",
                "notes": notes,
                "next_focus": "Blend recall of today's words with a denser reading passage.",
                "recommended_words": looked_up,
            },
            context=context,
        )

    async def generate_vocab_daily_mission(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if context.get("word_task_mission"):
            from aiforen.domain.vocab_learner_rhythm import build_coach_overview_lines
            from aiforen.domain.vocab_word_mission import (
                build_rules_word_mission_payload,
            )

            locale = str(context.get("locale") or "vi").lower()
            rhythm = str(context.get("learner_rhythm") or "early")
            activity = context.get("activity") or {}
            weaknesses = context.get("weaknesses") or []
            top_weakness = weaknesses[0] if weaknesses else None
            payload = build_rules_word_mission_payload(context)
            if not payload.get("coach_overview_lines"):
                payload["coach_overview_lines"] = build_coach_overview_lines(
                    rhythm=rhythm,  # type: ignore[arg-type]
                    locale=locale,
                    streak=int(activity.get("streak_days") or 0),
                    active_days_14=int(activity.get("active_days_14") or 0),
                    total_progress_words=int(
                        (context.get("stats") or {}).get("total_progress_words") or 0
                    ),
                    learned_today=int(
                        (context.get("stats") or {}).get("learned_today") or 0
                    ),
                    due_today=int((context.get("stats") or {}).get("due_today") or 0),
                    primary_weakness_label=(
                        top_weakness.get("label") if top_weakness else None
                    ),
                )
            payload["confidence"] = 0.74
            return payload

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

    async def evaluate_vocab_quiz(
        self,
        *,
        task_type: str,
        prompt: str,
        context: str,
        learner_answer: str,
        target_word: str,
        model_answer: str,
        source_sentence: str = "",
        rubric: list[str] | None = None,
        accepted_flexibility: str = "",
        ai_scoring: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        from .json_utils import normalize_vocab_quiz_ai_feedback

        _ = (prompt, context, source_sentence, rubric, accepted_flexibility)
        s = learner_answer.strip()
        word = target_word.lower()
        max_score = int((ai_scoring or {}).get("max_score") or 5)
        if not s:
            status = "fail"
            score = 0
        elif word not in s.lower():
            status = "fail"
            score = 1
        elif len(s.split()) < 4:
            status = "needs_fix"
            score = 3
        else:
            model_norm = model_answer.strip().lower()
            given_norm = s.lower()
            if model_norm and (model_norm == given_norm or model_norm in given_norm):
                status = "ok"
                score = max_score
            else:
                status = "ok"
                score = max(4, max_score - 1)

        corrected = s
        score_explanation = ""
        score_breakdown: list[dict[str, Any]] = []
        recommendation = ""
        if score >= max_score:
            recommendation = f"Good use of '{target_word}'."
        elif score >= 4:
            corrected = model_answer.strip() or s
            score_explanation = (
                f"You earned {score}/{max_score}: meaning is mostly right, "
                f"but wording is not as natural as a strong answer."
            )
            recommendation = (
                f"Your phrase «{s[:80]}» is understandable but awkward. "
                f"Try «{corrected[:80]}» because it matches the prompt more naturally."
            )
            score_breakdown = [
                {
                    "criterion": "meaning",
                    "points": max_score - 1,
                    "note": "Core meaning is clear.",
                },
                {
                    "criterion": "naturalness",
                    "points": score,
                    "note": "Word order or collocation could be smoother.",
                },
            ]
        else:
            recommendation = f"Rewrite using '{target_word}' naturally for this prompt."

        return normalize_vocab_quiz_ai_feedback(
            {
                "status": status,
                "score": score,
                "passed": status == "ok"
                or score >= int((ai_scoring or {}).get("pass_score") or 4),
                "uses_target_word": word in s.lower(),
                "answers_task": len(s.split()) >= 3,
                "corrected_sentence": corrected,
                "recommendation": recommendation,
                "score_explanation": score_explanation,
                "score_breakdown": score_breakdown,
            },
            learner_answer=learner_answer,
            model_answer=model_answer,
            task_type=task_type,
            ai_scoring=ai_scoring,
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
