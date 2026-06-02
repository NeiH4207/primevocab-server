"""Shared JSON extraction and vocab-eval prompt/response shaping for LLM providers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from aiforen.domain.vocab_mission_priority import reorder_plan_blocks


def extract_json(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("LLM response did not contain JSON")
    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON payload is not an object")
    return obj


def build_vocab_eval_prompt(
    *,
    word: str,
    translate_prompt: str,
    topic_prompt: str,
    translate_sentence: str,
    topic_sentence: str,
    current_band: float,
    target_band: float,
    weakness_context: Optional[list[str]] = None,
) -> str:
    weakness_block = ""
    if weakness_context:
        weakness_block = (
            f"\nKnown learner weakness patterns: {', '.join(weakness_context)}\n"
            "Pay extra attention to these patterns when evaluating.\n"
        )
    topic_block = ""
    if topic_sentence.strip():
        topic_block = (
            f'TOPIC task:\nTopic prompt: "{topic_prompt}"\n'
            f'Learner sentence: "{topic_sentence}"\n'
        )
    else:
        topic_block = "TOPIC task: not provided — omit topic evaluation; set topic fields to skipped in logic.\n"
    return (
        "You are an IELTS vocabulary coach for Vietnamese learners.\n"
        f"Target level: Band {current_band} → {target_band}\n"
        f'Target word: "{word}"\n\n'
        "Evaluate the learner's English sentence(s).\n\n"
        f'TRANSLATE task:\nVietnamese prompt: "{translate_prompt}"\n'
        f'Learner sentence: "{translate_sentence or ""}"\n\n'
        f"{topic_block}\n"
        "Language: Vietnamese-first learners; keep feedback direct and coach-like. "
        "No generic praise. No report-card tone.\n"
        f"{weakness_block}\n"
        "EVALUATION RULES\n"
        'For each provided sentence, status must be "pass", "needs_fix", or "fail".\n'
        "- pass: grammatically correct and target word used correctly in context\n"
        "- needs_fix: minor grammar/word form/collocation issue; target word present and mostly correct\n"
        "- fail: wrong meaning, Vietnamese text, target word absent, or seriously misused\n\n"
        "CORRECTED SENTENCE: minimal edit only; if pass, return learner sentence unchanged.\n"
        "RECOMMENDATION: max 2 sentences; quote the exact phrase; be specific. "
        "Pattern: Your phrase 'X' is [issue]. Try 'Y' because [reason].\n"
        f'BAND_STYLE_TIP: one concrete Band {target_band} tip referencing "{word}"; '
        "empty only if all sentences fail.\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "translate": {"status":"pass|needs_fix|fail","is_grammatically_ok":true,'
        '"answers_prompt":true,"uses_target_word":true,"corrected_sentence":"...",'
        '"recommendation":"..."},\n'
        '  "topic": {"status":"pass|needs_fix|fail","is_grammatically_ok":true,'
        '"answers_prompt":true,"uses_target_word":true,"corrected_sentence":"...",'
        '"recommendation":"..."},\n'
        '  "band_style_tip": "...",\n'
        '  "step3_passed": true\n'
        "}\n"
    )


_GENERIC_RECOMMENDATION_MARKERS = (
    "good job",
    "well done",
    "keep practicing",
    "keep up the good work",
    "nice work",
    "great job",
    "try again",
    "practice more",
)

_QUIZ_GENERIC_RECOMMENDATION_MARKERS = _GENERIC_RECOMMENDATION_MARKERS + (
    "gần được",
    "gần đúng",
    "almost there",
    "close enough",
    "not bad",
    "keep going",
)

_QUIZ_SCORE_CRITERIA_DEFAULT = (
    "meaning",
    "target_word",
    "grammar",
    "naturalness",
)

_QUIZ_SCORE_CRITERIA_TRANSLATE_HINTS = (
    "meaning",
    "target_word",
    "grammar",
    "naturalness",
)


def _clamp_corrected_sentence(corrected: str, original: str) -> str:
    original = (original or "").strip()
    corrected = (corrected or "").strip()
    if not corrected:
        return original
    if not original:
        return corrected[:280]
    max_len = max(len(original) + 40, int(len(original) * 1.35))
    if len(corrected) <= max_len:
        return corrected
    return original or corrected[:max_len]


def _sanitize_recommendation(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if len(lowered) < 12:
        return cleaned
    if len(lowered) < 48 and any(
        marker in lowered for marker in _GENERIC_RECOMMENDATION_MARKERS
    ):
        return ""
    return cleaned


def _sanitize_quiz_recommendation(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _QUIZ_GENERIC_RECOMMENDATION_MARKERS):
        if len(lowered) < 64:
            return ""
    if len(lowered) < 12:
        return cleaned
    return cleaned


def _quiz_score_criteria(task_type: str) -> tuple[str, ...]:
    tt = (task_type or "").strip().lower()
    if tt in ("translate", "translate_with_hints"):
        return _QUIZ_SCORE_CRITERIA_TRANSLATE_HINTS
    return _QUIZ_SCORE_CRITERIA_DEFAULT


def _normalize_score_breakdown(
    raw: Any,
    *,
    max_score: int,
    criteria: tuple[str, ...],
) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:6]:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion") or "").strip()[:48]
        note = str(item.get("note") or "").strip()[:280]
        if not criterion or not note:
            continue
        try:
            points = int(item.get("points", 0))
        except (TypeError, ValueError):
            points = 0
        points = max(0, min(max_score, points))
        out.append({"criterion": criterion, "points": points, "note": note})
    if out:
        return out
    return []


def _synthesize_vocab_quiz_score_fallback(
    *,
    learner_answer: str,
    corrected_sentence: str,
    model_answer: str,
    score: int,
    max_score: int,
) -> tuple[str, str]:
    learner = (learner_answer or "").strip()
    corrected = (corrected_sentence or "").strip()
    model = (model_answer or "").strip()
    if corrected and learner and corrected.lower() != learner.lower():
        explanation = f"Bạn được {score}/{max_score} điểm vì câu cần chỉnh nhẹ so với bản bạn viết."
        recommendation = (
            f"Cụm trong câu của bạn chưa khớp hoàn toàn. "
            f"Thử: «{corrected}»" + (f" — gần với «{model}»." if model else ".")
        )
        return explanation, recommendation
    if model and learner and model.lower() != learner.lower():
        explanation = f"Bạn được {score}/{max_score} điểm; câu đúng hướng nhưng chưa đạt mức tối đa."
        recommendation = (
            f"Câu của bạn: «{learner[:120]}». "
            f"Tham khảo: «{model[:120]}» — chỉnh lại cho tự nhiên và đúng nghĩa đề."
        )
        return explanation, recommendation
    explanation = (
        f"Bạn được {score}/{max_score} điểm; còn thiếu một phần nhỏ để đạt điểm tối đa."
    )
    recommendation = "Rà lại nghĩa đề, từ mục tiêu, ngữ pháp và cách diễn đạt tự nhiên."
    return explanation, recommendation


def _norm_eval_entry(node: Dict[str, Any], fallback_sentence: str) -> Dict[str, Any]:
    node = node or {}
    raw_status = str(node.get("status", "")).lower().strip()
    if raw_status == "pass":
        raw_status = "ok"
    if raw_status not in ("ok", "needs_fix", "fail"):
        # Back-compat with older is_grammatically_ok-only payloads
        raw_status = "ok" if node.get("is_grammatically_ok") else "needs_fix"
    answers_prompt = bool(node.get("answers_prompt", raw_status != "fail"))
    uses_target_word = bool(node.get("uses_target_word", raw_status != "fail"))
    if not answers_prompt or not uses_target_word:
        raw_status = "fail"
    corrected = _clamp_corrected_sentence(
        str(node.get("corrected_sentence", fallback_sentence)),
        fallback_sentence,
    )
    recommendation = _sanitize_recommendation(str(node.get("recommendation", "")))
    return {
        "status": raw_status,
        "is_grammatically_ok": raw_status == "ok",
        "answers_prompt": answers_prompt,
        "uses_target_word": uses_target_word,
        "corrected_sentence": corrected,
        "recommendation": recommendation,
    }


def step3_passed_from_feedback(ai_feedback: Optional[Dict[str, Any]]) -> bool:
    if not ai_feedback or ai_feedback.get("ai_status") in (
        "unavailable",
        "invalid_language",
    ):
        return False
    if "step3_passed" in ai_feedback:
        return bool(ai_feedback.get("step3_passed"))
    translate = ai_feedback.get("translate") or {}
    topic = ai_feedback.get("topic") or {}
    if topic.get("status") == "skipped":
        return translate.get("status") == "ok"
    return translate.get("status") == "ok" and topic.get("status") == "ok"


def normalize_vocab_eval_payload(
    payload: Dict[str, Any],
    *,
    translate_sentence: str,
    topic_sentence: str,
) -> Dict[str, Any]:
    translate = _norm_eval_entry(payload.get("translate") or {}, translate_sentence)
    topic = _norm_eval_entry(payload.get("topic") or {}, topic_sentence)
    if not topic_sentence.strip():
        topic = {
            "status": "skipped",
            "is_grammatically_ok": True,
            "answers_prompt": True,
            "uses_target_word": True,
            "corrected_sentence": "",
            "recommendation": "",
        }
    step3_passed = bool(payload.get("step3_passed"))
    if not step3_passed:
        if topic.get("status") == "skipped":
            step3_passed = translate["status"] == "ok"
        else:
            step3_passed = translate["status"] == "ok" and topic["status"] == "ok"
    return {
        "translate": translate,
        "topic": topic,
        "band_style_tip": str(payload.get("band_style_tip", "")),
        "step3_passed": step3_passed,
    }


def build_vocab_quiz_eval_prompt(
    *,
    task_type: str,
    prompt: str,
    context: str,
    learner_answer: str,
    target_word: str,
    model_answer: str,
    source_sentence: str = "",
    rubric: Optional[List[str]] = None,
    accepted_flexibility: str = "",
    ai_scoring: Optional[Dict[str, Any]] = None,
) -> str:
    rubric_lines = "\n".join(
        f"- {line}" for line in (rubric or []) if str(line).strip()
    )
    scoring = ai_scoring or {}
    max_score = int(scoring.get("max_score") or 5)
    pass_score = int(scoring.get("pass_score") or 4)
    flex = (accepted_flexibility or "").strip()
    flex_block = f"\nAcceptance note: {flex}\n" if flex else ""
    context_block = (
        f'\nContext / Vietnamese prompt: "{context.strip()}"\n'
        if context.strip()
        else ""
    )
    source_block = (
        f'\nSource sentence to preserve: "{source_sentence.strip()}"\n'
        if source_sentence.strip()
        else ""
    )
    rubric_block = f"\nRubric:\n{rubric_lines}\n" if rubric_lines else ""
    criteria = _quiz_score_criteria(task_type)
    criteria_line = ", ".join(criteria)
    return (
        "You are an English vocabulary coach grading ONE learner production answer.\n"
        "Respect the task's CEFR or exam-track level: reward natural, level-fit usage rather than IELTS-only phrasing.\n"
        f'Task type: "{task_type}"\n'
        f'Target word: "{target_word}"\n'
        f'Instruction shown to learner: "{prompt.strip()}"\n'
        f"{context_block}"
        f"{source_block}"
        f'Learner answer: "{learner_answer.strip()}"\n'
        f'Reference answer (one strong example, NOT the only acceptable answer): "{model_answer.strip()}"\n'
        f"{flex_block}"
        f"{rubric_block}\n"
        "Rules:\n"
        "- Learner must write ENGLISH (not Vietnamese).\n"
        "- Accept any natural answer that satisfies the task, uses the target word correctly, "
        "and preserves the source meaning when rewriting.\n"
        "- Do NOT require exact wording match with the reference answer.\n"
        "- status: ok = pass, needs_fix = minor issues but mostly correct, fail = wrong/missing target/off-task.\n"
        f"- score: integer 0..{max_score}; passed: true when score >= {pass_score}.\n"
        "- corrected_sentence: minimal edit; if fully correct, return learner answer unchanged.\n"
        "- When score == max_score: recommendation may be brief praise (1 sentence).\n"
        f"- When score < {max_score}: recommendation MUST explain exactly why points were lost — "
        "quote the learner's exact phrase, name the issue (meaning / target word / grammar / naturalness), "
        "and suggest a fix. Pattern: Your phrase 'X' is [issue]. Try 'Y' because [reason]. "
        "Vietnamese is OK in recommendation and score_explanation.\n"
        f"- score_explanation: 1-3 sentences summarizing why the score is not {max_score} "
        "(required when score < max_score; empty when score == max_score).\n"
        f"- score_breakdown: optional array (max 4 items) when score < {max_score}; each item "
        f'{{"criterion": "<one of: {criteria_line}>", "points": <0..{max_score}>, "note": "why this dimension lost points"}}. '
        "Points are partial credit for that dimension, not a sum that must equal score.\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "status": "ok|needs_fix|fail",\n'
        f'  "score": 0,\n'
        '  "passed": false,\n'
        '  "uses_target_word": true,\n'
        '  "answers_task": true,\n'
        '  "corrected_sentence": "...",\n'
        '  "recommendation": "...",\n'
        '  "score_explanation": "...",\n'
        '  "score_breakdown": [{"criterion":"meaning","points":1,"note":"..."}]\n'
        "}\n"
    )


def normalize_vocab_quiz_ai_feedback(
    payload: Dict[str, Any],
    *,
    learner_answer: str,
    model_answer: str,
    task_type: str = "",
    ai_scoring: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    scoring = ai_scoring or {}
    max_score = max(1, int(scoring.get("max_score") or 5))
    pass_score = max(1, min(max_score, int(scoring.get("pass_score") or 4)))
    raw_status = str(payload.get("status", "")).lower().strip()
    if raw_status == "pass":
        raw_status = "ok"
    if raw_status not in ("ok", "needs_fix", "fail"):
        raw_status = "ok" if payload.get("passed") else "needs_fix"

    score_raw = payload.get("score")
    if score_raw is None:
        score = (
            max_score
            if raw_status == "ok"
            else (pass_score - 1 if raw_status == "needs_fix" else 0)
        )
    else:
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            score = 0
    score = max(0, min(max_score, score))

    uses_target = bool(payload.get("uses_target_word", raw_status != "fail"))
    answers_task = bool(payload.get("answers_task", raw_status != "fail"))
    if not uses_target or not answers_task:
        raw_status = "fail"
        score = min(score, pass_score - 1)

    if "passed" in payload:
        passed = bool(payload.get("passed"))
    elif scoring:
        passed = score >= pass_score
    else:
        passed = raw_status == "ok"

    if passed and raw_status == "fail":
        raw_status = "needs_fix"
    if not passed and raw_status == "ok" and score < pass_score:
        raw_status = "needs_fix"

    corrected_sentence = _clamp_corrected_sentence(
        str(payload.get("corrected_sentence", learner_answer)),
        learner_answer,
    )
    recommendation = _sanitize_quiz_recommendation(
        str(payload.get("recommendation", ""))
    )
    score_explanation = str(payload.get("score_explanation") or "").strip()[:400]
    resolved_task_type = str(task_type or payload.get("task_type") or "")
    score_breakdown = _normalize_score_breakdown(
        payload.get("score_breakdown"),
        max_score=max_score,
        criteria=_quiz_score_criteria(resolved_task_type),
    )

    if score < max_score:
        if not score_explanation and recommendation:
            score_explanation = recommendation[:400]
        if not recommendation or (
            len(recommendation) < 64
            and any(
                marker in recommendation.lower()
                for marker in _QUIZ_GENERIC_RECOMMENDATION_MARKERS
            )
        ):
            fb_explanation, fb_recommendation = _synthesize_vocab_quiz_score_fallback(
                learner_answer=learner_answer,
                corrected_sentence=corrected_sentence,
                model_answer=model_answer,
                score=score,
                max_score=max_score,
            )
            if not score_explanation:
                score_explanation = fb_explanation
            if not recommendation:
                recommendation = fb_recommendation
    else:
        score_explanation = ""
        score_breakdown = []

    result: Dict[str, Any] = {
        "ai_status": "ok",
        "passed": passed,
        "score": score,
        "max_score": max_score,
        "pass_score": pass_score,
        "status": raw_status,
        "recommendation": recommendation,
        "corrected_sentence": corrected_sentence,
        "model_answer": model_answer,
    }
    if score_explanation:
        result["score_explanation"] = score_explanation
    if score_breakdown:
        result["score_breakdown"] = score_breakdown
    return result


def _vocab_mission_language_rules(locale: str) -> str:
    loc = (locale or "vi").lower()
    if loc.startswith("vi"):
        return (
            "Language (REQUIRED):\n"
            "- Write headline, summary, every plan_blocks title/description, and primary_cta.label in Vietnamese.\n"
            "- Keep in English (do not translate): band labels (e.g. Band 5, Band 6.5), pack titles/categories from candidate_packs, "
            "weakness labels and stat_labels, vocabulary words, and standard study terms "
            "(review, due, mastery, IELTS, GRE, MCQ, production practice, recall).\n"
            "- Mix naturally: Vietnamese coaching sentences with English keywords where learners expect them.\n"
            "- Tone: concise, actionable, for Vietnamese learners preparing for IELTS.\n"
            "- Do NOT write headline/summary/plan_blocks/CTA in English when locale is vi.\n"
        )
    return "- Write all user-facing text in English.\n"


def build_vocab_daily_word_mission_prompt(*, context: Dict[str, Any]) -> str:
    locale = str(context.get("locale") or "vi")
    language_rules = _vocab_mission_language_rules(locale)
    word_range = context.get("word_count_range") or {"min": 3, "max": 8}
    return (
        "You are a vocabulary learning coach designing today's word-by-word mission.\n"
        "Return ONLY valid JSON (no markdown).\n\n"
        "Important: the backend rule engine generates headline, summary, reason, expected_gain, and CTA.\n"
        'Do NOT write those. Return headline="", summary="", primary_cta={}.\n\n'
        "MODEL: One vocabulary word = one task. All tasks today share ONE session_template_id "
        "(same task_steps and difficulty style).\n\n"
        "Your job:\n"
        "1. Read task_template_catalog — pick exactly one session_template_id that fits "
        "weaknesses, due_today, learner_rhythm, and band.\n"
        "2. Write selection_rationale (2–4 short sentences: why this template and word count).\n"
        "3. Set target_word_count within word_count_range (integer).\n"
        "4. List word_tasks: prioritize recent wrong answers, then due, then new words. "
        "No duplicate words. word_id optional if unknown (backend fills from pool).\n"
        "5. Write 2–3 coach_overview_lines (max 95 chars each).\n"
        "6. Return confidence.\n\n"
        "WORD_TASKS:\n"
        "- source: wrong_answer | due | new | pool | calibration\n"
        "- priority: 1 = do first\n"
        "- lemma_hint: English lemma when word_id unknown\n"
        "- note: optional 1 line for coach (not shown as block title)\n\n"
        "CONFIDENCE: base 0.70; +0.10 if top weakness evidence_count>=3; "
        '+0.10 if learner_rhythm is "consistent"; clamp 0.45–0.92.\n\n'
        "Schema:\n"
        "{\n"
        '  "coach_overview_lines": ["...", "..."],\n'
        '  "session_template_id": "<from catalog>",\n'
        '  "selection_rationale": "...",\n'
        f'  "target_word_count": {word_range.get("min", 5)},\n'
        '  "word_tasks": [{"word_id":"","lemma_hint":"","source":"wrong_answer","priority":1,"note":""}],\n'
        '  "confidence": 0.7,\n'
        '  "headline": "",\n'
        '  "summary": "",\n'
        '  "primary_cta": {}\n'
        "}\n"
        f"{language_rules}\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}\n"
    )


def build_vocab_daily_mission_prompt(*, context: Dict[str, Any]) -> str:
    if context.get("word_task_mission"):
        return build_vocab_daily_word_mission_prompt(context=context)
    locale = str(context.get("locale") or "vi")
    language_rules = _vocab_mission_language_rules(locale)
    return (
        "You are a vocabulary learning coach writing a daily mission briefing.\n"
        "Return ONLY valid JSON (no markdown).\n\n"
        "Important: the backend rule engine generates headline, summary, reason, expected_gain, and CTA.\n"
        'Do NOT write those. Return headline="", summary="", primary_cta={}.\n\n'
        "Your job:\n"
        "1. Write 2–3 coach_overview_lines (max 95 chars each).\n"
        "2. Choose plan_blocks structure (2–4 blocks).\n"
        "3. Return confidence.\n\n"
        "COACH_OVERVIEW_LINES:\n"
        "- Line 1: one honest observation with a number from context.\n"
        "- Line 2: encouragement tied to streak, rhythm, or recent pattern.\n"
        "- Optional line 3: micro-outcome for today's session only.\n"
        "- No generic motivation. No enum keys in text.\n\n"
        "PLAN_BLOCKS:\n"
        "- type: repair_weakness | review_due | study_pack | production_practice\n"
        "- title and description: leave empty strings (backend rewrites).\n"
        "- pack_id from candidate_packs only.\n"
        "- target_count realistic (words 5–8, sentences 3–5 for production_practice).\n"
        "- Priority: recent mistake repair first; review_due after repair unless backlog is heavy; "
        "production_practice usually last.\n\n"
        "CONFIDENCE: base 0.70; +0.10 if top weakness evidence_count>=3; "
        '+0.10 if learner_rhythm is "consistent"; -0.20 if no weaknesses and due_today=0; clamp 0.45–0.92.\n\n'
        "Schema:\n"
        "{\n"
        '  "coach_overview_lines": ["...", "..."],\n'
        '  "plan_blocks": [{"type":"...", "pack_id":"...", "target_count":3, "title":"", "description":"", "word_ids":[], "task_steps":["mcq"]}],\n'
        '  "confidence": 0.7,\n'
        '  "headline": "",\n'
        '  "summary": "",\n'
        '  "primary_cta": {}\n'
        "}\n"
        f"{language_rules}\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}\n"
    )


_MISSION_WEAKNESS_LABELS: Dict[str, str] = {
    "stale_review_due": "due reviews",
    "meaning_mcq_wrong": "Meaning MCQ",
    "translation_failed": "sentence practice",
    "topic_sentence_failed": "sentence practice",
    "production_practice": "sentence practice",
    "low_mastery_band": "low mastery band",
}


def sanitize_mission_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    import re

    text = re.sub(r"\s*\([a-z0-9_]+\)", "", text, flags=re.IGNORECASE)

    def _replace_enum(match: re.Match[str]) -> str:
        key = match.group(0).lower()
        return _MISSION_WEAKNESS_LABELS.get(key, key.replace("_", " "))

    text = re.sub(
        r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", _replace_enum, text, flags=re.IGNORECASE
    )
    for key, label in _MISSION_WEAKNESS_LABELS.items():
        spaced = key.replace("_", " ")
        text = re.sub(re.escape(spaced), label, text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _clean_word_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:20] if str(item).strip()]


def _clean_task_steps(value: Any, fallback: list[str] | None = None) -> list[str]:
    allowed = {"learn", "mcq", "translate", "topic"}
    if not isinstance(value, list):
        return list(fallback or [])
    steps = []
    for item in value[:4]:
        step = str(item).strip().lower()
        if step in allowed and step not in steps:
            steps.append(step)
    return steps or list(fallback or [])


def _clean_plan_block(node: Dict[str, Any], allowed_packs: set[str]) -> Dict[str, Any]:
    block_type = str(node.get("type") or "study_pack").strip()
    if block_type not in {
        "review_due",
        "repair_weakness",
        "study_pack",
        "production_practice",
    }:
        block_type = "study_pack"
    pack_id = node.get("pack_id")
    pack_id = str(pack_id) if pack_id and str(pack_id) in allowed_packs else None
    target_count = node.get("target_count", 0)
    try:
        target_count = max(0, int(target_count))
    except Exception:
        target_count = 0
    return {
        "type": block_type,
        "title": sanitize_mission_text(str(node.get("title") or "Vocab practice"))[:48],
        "description": sanitize_mission_text(str(node.get("description") or ""))[:120],
        "target_count": target_count,
        "pack_id": pack_id,
        "word_ids": _clean_word_ids(node.get("word_ids")),
        "task_steps": _clean_task_steps(node.get("task_steps")),
    }


def normalize_vocab_daily_mission_payload(
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    if context.get("word_task_mission"):
        from aiforen.domain.vocab_word_mission import (
            normalize_vocab_daily_word_mission_payload,
        )

        return normalize_vocab_daily_word_mission_payload(payload, context=context)

    candidate_packs = context.get("candidate_packs") or []
    allowed_packs = {str(p.get("pack_id")) for p in candidate_packs if p.get("pack_id")}
    raw_blocks = payload.get("plan_blocks") or []
    blocks = [
        _clean_plan_block(block, allowed_packs)
        for block in raw_blocks
        if isinstance(block, dict)
    ][:4]
    if not blocks:
        raise ValueError("LLM mission has no valid plan_blocks")

    mission_type = str(
        (context.get("mission_signals") or {}).get("primary_mission_type")
        or "repair_weakness"
    )
    blocks = reorder_plan_blocks(blocks, mission_type=mission_type)  # type: ignore[arg-type]

    raw_cta = payload.get("primary_cta") or {}
    if not isinstance(raw_cta, dict):
        raw_cta = {}
    action_type = str(raw_cta.get("action_type") or blocks[0]["type"]).strip()
    if action_type not in {"review_due", "repair_weakness", "study_pack"}:
        action_type = "study_pack"
    pack_id = raw_cta.get("pack_id") or blocks[0].get("pack_id")
    pack_id = str(pack_id) if pack_id and str(pack_id) in allowed_packs else None
    confidence = payload.get("confidence", 0.7)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.7
    raw_coach = payload.get("coach_overview_lines")
    coach_lines: list[str] = []
    if isinstance(raw_coach, list):
        coach_lines = [
            sanitize_mission_text(str(line))[:95]
            for line in raw_coach[:3]
            if str(line).strip()
        ]

    result: Dict[str, Any] = {
        "headline": "",
        "summary": "",
        "confidence": max(0.0, min(1.0, confidence)),
        "plan_blocks": blocks,
        "primary_cta": {},
    }
    if coach_lines:
        result["coach_overview_lines"] = coach_lines
    return result


def _normalize_criterion(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    node = payload.get(key) or {}
    score = node.get("score", 0)
    feedback = node.get("feedback", "")
    try:
        score = float(score)
    except Exception:
        score = 0.0
    score = max(0.0, min(9.0, score))
    return {"score": score, "feedback": str(feedback)}


def normalize_writing_assessment(payload: Dict[str, Any]) -> Dict[str, Any]:
    task_achievement = _normalize_criterion(payload, "task_achievement")
    coherence_cohesion = _normalize_criterion(payload, "coherence_cohesion")
    lexical_resource = _normalize_criterion(payload, "lexical_resource")
    grammar_accuracy = _normalize_criterion(payload, "grammar_accuracy")

    scores = payload.get("scores") or {}
    overall = scores.get("overall_score")
    if overall is None:
        overall = (
            task_achievement["score"]
            + coherence_cohesion["score"]
            + lexical_resource["score"]
            + grammar_accuracy["score"]
        ) / 4
    try:
        overall = float(overall)
    except Exception:
        overall = 0.0

    return {
        "task_achievement": task_achievement,
        "coherence_cohesion": coherence_cohesion,
        "lexical_resource": lexical_resource,
        "grammar_accuracy": grammar_accuracy,
        "scores": {
            "task_achievement": task_achievement["score"],
            "coherence_cohesion": coherence_cohesion["score"],
            "lexical_resource": lexical_resource["score"],
            "grammar_accuracy": grammar_accuracy["score"],
            "overall_score": max(0.0, min(9.0, overall)),
        },
        "general_comments": str(payload.get("general_comments", "")),
        "improvement_suggestions": str(payload.get("improvement_suggestions", "")),
        "improvement_explanation": str(payload.get("improvement_explanation", "")),
        "next_level_sample": str(payload.get("next_level_sample", "")),
    }


_CALIBRATION_CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


def _clamp_text(value: Any, *, max_chars: int, fallback: str = "") -> str:
    text = str(value or "").strip() or fallback
    return text[:max_chars]


def _clean_str_list(
    value: Any,
    *,
    max_items: int,
    max_chars: int,
    fallback: Optional[List[str]] = None,
) -> List[str]:
    if not isinstance(value, list):
        return list(fallback or [])[:max_items]
    out = [str(item).strip() for item in value if str(item).strip()]
    if not out and fallback:
        out = list(fallback)
    return [item[:max_chars] for item in out[:max_items]]


def _clean_calibration_plan(
    value: Any,
    *,
    max_items: int,
    title_max: int,
    desc_max: int,
    fallback: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, str]] = []
    for item in rows[:max_items]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title[:title_max],
                "description": str(item.get("description") or "").strip()[:desc_max],
            }
        )
    if not out and fallback:
        return list(fallback)[:max_items]
    return out


def _clamp_float_near(value: Any, *, base: float, max_delta: float = 0.05) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = base
    low = max(0.0, base - max_delta)
    high = min(1.0, base + max_delta)
    return round(max(low, min(high, parsed)), 2)


def build_vocab_calibration_prompt(*, context: Dict[str, Any]) -> str:
    locale = str(context.get("locale") or "vi")
    vi = locale.lower().startswith("vi")
    rule = context.get("rule_estimate") or {}
    language_rules = (
        (
            "Language: Vietnamese-first. Keep Band, IELTS, vocab, recall, MCQ, sentence practice, pack, confidence in English when natural.\n"
            "Avoid formal Vietnamese and report-card tone.\n"
        )
        if vi
        else "Language: English. Product coach tone.\n"
    )
    return (
        "You are a product-focused IELTS vocabulary coach writing a compact onboarding result screen.\n"
        "The rule engine already estimated the learner's vocabulary level.\n"
        "Do NOT re-estimate the learner. Do NOT change CEFR, IELTS band, confidence, or recommended_pack_id.\n\n"
        "Turn the calibration result into concise UI copy that helps the learner start the recommended path immediately.\n\n"
        f"Fixed rule_estimate: {json.dumps(rule, ensure_ascii=False, default=str)}\n"
        f"answer_distribution: {json.dumps(context.get('answer_distribution') or {}, ensure_ascii=False)}\n"
        f"word_examples_by_level: {json.dumps(context.get('word_examples_by_level') or {}, ensure_ascii=False)}\n\n"
        "UI constraints:\n"
        "- headline max 42 chars\n"
        "- summary max 140 chars\n"
        "- strengths exactly 2 items, max 90 chars each\n"
        "- weak_spots exactly 2 items, max 90 chars each\n"
        "- recommended_plan exactly 3 items; title max 30; description max 80\n"
        "- primary_cta_label max 28 chars (e.g. Start Band 7 path)\n\n"
        f"{language_rules}\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "headline": "string",\n'
        '  "summary": "string",\n'
        '  "strengths": ["string", "string"],\n'
        '  "weak_spots": ["string", "string"],\n'
        '  "recommended_plan": [{"title":"string","description":"string"}, ...],\n'
        '  "primary_cta_label": "string",\n'
        '  "confidence": 0.0\n'
        "}\n\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}\n"
    )


def postprocess_calibration_llm_payload(
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any],
    rule_result: Dict[str, Any],
) -> Dict[str, Any]:
    from loguru import logger

    if (
        payload.get("cefr_level")
        and str(payload.get("cefr_level")).upper()
        != str(rule_result.get("cefr_level") or "").upper()
    ):
        logger.warning(
            "Calibration LLM tried to override CEFR: {} -> {}",
            rule_result.get("cefr_level"),
            payload.get("cefr_level"),
        )

    band = (
        rule_result.get("estimated_band") or rule_result.get("ielts_band_hint") or 6.0
    )
    try:
        band_f = float(band)
    except Exception:
        band_f = 6.0
    default_cta = f"Start Band {band_f:.1f} path"

    base_conf = float(rule_result.get("confidence") or 0.6)
    return {
        "headline": _clamp_text(
            payload.get("headline"),
            max_chars=42,
            fallback=str(rule_result.get("headline") or ""),
        ),
        "summary": _clamp_text(
            payload.get("summary"),
            max_chars=140,
            fallback=str(rule_result.get("summary") or ""),
        ),
        "strengths": _clean_str_list(
            payload.get("strengths"),
            max_items=2,
            max_chars=90,
            fallback=(rule_result.get("strengths") or [])[:2],
        ),
        "weak_spots": _clean_str_list(
            payload.get("weak_spots"),
            max_items=2,
            max_chars=90,
            fallback=(rule_result.get("weak_spots") or [])[:2],
        ),
        "recommended_plan": _clean_calibration_plan(
            payload.get("recommended_plan"),
            max_items=3,
            title_max=30,
            desc_max=80,
            fallback=(rule_result.get("recommended_plan") or [])[:3],
        ),
        "primary_cta_label": _clamp_text(
            payload.get("primary_cta_label"),
            max_chars=28,
            fallback=default_cta,
        ),
        "cefr_level": rule_result.get("cefr_level"),
        "estimated_band": rule_result.get("estimated_band"),
        "ielts_band_hint": rule_result.get("ielts_band_hint"),
        "recommended_pack_id": rule_result.get("recommended_pack_id"),
        "confidence": _clamp_float_near(
            payload.get("confidence"), base=base_conf, max_delta=0.05
        ),
    }


def normalize_vocab_calibration_payload(
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    rule = context.get("rule_estimate") or {}
    rule_cefr = str(rule.get("cefr_level") or "B1").upper().strip()
    cefr = str(payload.get("cefr_level") or rule_cefr).upper().strip()
    if cefr not in _CALIBRATION_CEFR_LEVELS:
        cefr = rule_cefr
    if rule_cefr in _CALIBRATION_CEFR_LEVELS:
        cefr = rule_cefr

    try:
        confidence = float(payload.get("confidence", rule.get("confidence", 0.5)))
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    plan = payload.get("recommended_plan") or []
    if not isinstance(plan, list):
        plan = []
    normalized_plan = []
    for item in plan[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        desc = str(item.get("description") or "").strip()
        if title:
            normalized_plan.append({"title": title[:80], "description": desc[:160]})

    strengths = [
        str(s).strip() for s in (payload.get("strengths") or []) if str(s).strip()
    ][:2]
    weak_spots = [
        str(s).strip() for s in (payload.get("weak_spots") or []) if str(s).strip()
    ][:2]

    headline = str(payload.get("headline") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    locale = str(context.get("locale") or "vi")
    vi = locale.lower().startswith("vi")
    if not headline:
        headline = (
            f"Trình độ từ vựng khoảng {cefr}" if vi else f"Vocabulary around {cefr}"
        )
    if not summary:
        summary = (
            f"Bạn đang ở mức {cefr}. Bắt đầu pack phù hợp và ôn các từ chưa chắc."
            if vi
            else f"You are around {cefr}. Start a matching pack and repair uncertain words."
        )

    return {
        "headline": headline[:120],
        "summary": summary[:220],
        "cefr_level": cefr,
        "confidence": round(confidence, 2),
        "strengths": strengths,
        "weak_spots": weak_spots,
        "recommended_plan": normalized_plan,
    }
