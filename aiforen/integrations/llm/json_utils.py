"""Shared JSON extraction and vocab-eval prompt/response shaping for LLM providers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from aiforen.domain.vocab_mission_priority import reorder_plan_blocks


def _strip_markdown_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _repair_json_text(text: str) -> str:
    """Remove trailing commas before closing braces/brackets."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_balanced_json_object(raw: str) -> Optional[str]:
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _parse_json_object(text: str) -> Dict[str, Any]:
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON payload is not an object")
    return obj


def extract_json(raw: str) -> Dict[str, Any]:
    trimmed = _strip_markdown_fences(raw)
    candidates = [trimmed]
    balanced = _extract_balanced_json_object(trimmed)
    if balanced and balanced != trimmed:
        candidates.append(balanced)

    last_exc: Optional[Exception] = None
    for candidate in candidates:
        for attempt in (candidate, _repair_json_text(candidate)):
            try:
                return _parse_json_object(attempt)
            except Exception as exc:
                last_exc = exc

    raise ValueError("LLM response did not contain JSON") from last_exc


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


# ---------------------------------------------------------------------------
# Vocab Coaching: phrase explanation, reading questions, coaching notes
# ---------------------------------------------------------------------------

_COACHING_QUESTION_TYPES = {"comprehension", "vocabulary", "context", "inference"}


def build_reading_explain_prompt(*, context: Dict[str, Any]) -> str:
    return (
        "You are an IELTS reading coach. A learner selected a phrase inside a passage and "
        "wants a short, concrete explanation that connects the phrase's vocabulary to the "
        "wider meaning of the sentence.\n\n"
        f"Learner CEFR level: {context.get('level') or 'B1'}\n"
        f"Selected phrase: {json.dumps(context.get('phrase') or '', ensure_ascii=False)}\n"
        f"Sentence: {json.dumps(context.get('sentence') or '', ensure_ascii=False)}\n"
        f"Paragraph context: {json.dumps((context.get('paragraph') or '')[:1800], ensure_ascii=False)}\n"
        f"Passage title: {json.dumps(context.get('title') or '', ensure_ascii=False)}\n\n"
        f"Grouped reading behaviour: {json.dumps(context.get('action_summary') or {}, ensure_ascii=False)}\n\n"
        "Constraints:\n"
        "- explanation max 280 chars, plain and encouraging, no jargon\n"
        "- paraphrase max 160 chars: rewrite the phrase in simpler English\n"
        "- vocab_notes: 1-3 short items (max 80 chars each) on key words/collocations\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "explanation": "string",\n'
        '  "paraphrase": "string",\n'
        '  "vocab_notes": ["string"]\n'
        "}\n"
    )


def normalize_reading_explain_payload(
    payload: Dict[str, Any],
    *,
    phrase: str,
    sentence: str,
    level: str,
) -> Dict[str, Any]:
    explanation = _clamp_text(
        payload.get("explanation"),
        max_chars=320,
        fallback=(
            f'At {level} level, read "{phrase}" inside its sentence: "{sentence}". '
            "Notice which word carries the main idea, then paraphrase it in simpler English."
        ),
    )
    paraphrase = _clamp_text(payload.get("paraphrase"), max_chars=180, fallback="")
    notes = _clean_str_list(
        payload.get("vocab_notes"), max_items=3, max_chars=80, fallback=[]
    )
    return {
        "explanation": explanation,
        "paraphrase": paraphrase,
        "vocab_notes": notes,
    }


def build_reading_questions_prompt(*, context: Dict[str, Any]) -> str:
    return (
        "You are an IELTS reading examiner. Generate targeted multiple-choice questions for a "
        "learner based on a passage AND the learner's own reading behaviour (the words they "
        "looked up, phrases they highlighted, words they bolded, and any wrong answers). "
        "Prioritise the vocabulary and ideas the learner interacted with — these reveal what "
        "they need to learn.\n\n"
        f"Learner CEFR level: {context.get('level') or 'B1'}\n"
        f"Passage title: {json.dumps(context.get('title') or '', ensure_ascii=False)}\n"
        f"Passage excerpt: {json.dumps((context.get('passage') or '')[:2400], ensure_ascii=False)}\n"
        f"Looked-up words: {json.dumps(context.get('looked_up_words') or [], ensure_ascii=False)}\n"
        f"Translated phrases: {json.dumps(context.get('translated_phrases') or [], ensure_ascii=False)}\n"
        f"Selected phrases: {json.dumps(context.get('selected_phrases') or [], ensure_ascii=False)}\n"
        f"Highlighted phrases: {json.dumps(context.get('highlighted_phrases') or [], ensure_ascii=False)}\n"
        f"Explained phrases: {json.dumps(context.get('explained_phrases') or [], ensure_ascii=False)}\n"
        f"Bolded words: {json.dumps(context.get('bolded_words') or [], ensure_ascii=False)}\n"
        f"Difficult/over-band words: {json.dumps(context.get('difficult_words') or [], ensure_ascii=False)}\n"
        f"Previous wrong answers: {json.dumps(context.get('wrong_answers') or [], ensure_ascii=False)}\n\n"
        f"Grouped reading behaviour: {json.dumps(context.get('action_summary') or {}, ensure_ascii=False)}\n\n"
        f"Generate exactly {int(context.get('count') or 4)} questions.\n"
        "Each question: type in [comprehension, vocabulary, context, inference]; 3-4 options; "
        "exactly one correct option text that is also present in options; a short explanation; "
        "and source_word when the question targets a specific word.\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "questions": [\n'
        '    {"type":"vocabulary","prompt":"string","options":["a","b","c"],'
        '"correct_option":"a","explanation":"string","source_word":"string"}\n'
        "  ]\n"
        "}\n"
    )


def normalize_reading_questions_payload(
    payload: Dict[str, Any],
    *,
    count: int,
    fallback_questions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    raw = payload.get("questions")
    rows = raw if isinstance(raw, list) else []
    out: List[Dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        options = [
            str(opt).strip() for opt in (item.get("options") or []) if str(opt).strip()
        ][:4]
        correct = str(item.get("correct_option") or "").strip()
        if not prompt or len(options) < 2:
            continue
        if correct not in options:
            correct = options[0]
        qtype = str(item.get("type") or "comprehension").strip().lower()
        if qtype not in _COACHING_QUESTION_TYPES:
            qtype = "comprehension"
        out.append(
            {
                "id": f"aiq-{index + 1}",
                "type": qtype,
                "prompt": prompt[:240],
                "options": options,
                "correct_option": correct,
                "explanation": str(item.get("explanation") or "").strip()[:240],
                "source_word": str(item.get("source_word") or "").strip()[:64] or None,
                "generated": True,
            }
        )
        if len(out) >= count:
            break
    if not out and fallback_questions:
        return {"questions": list(fallback_questions)[:count]}
    return {"questions": out}


def build_coaching_notes_prompt(*, context: Dict[str, Any]) -> str:
    locale = str(context.get("locale") or "en")
    vi = locale.lower().startswith("vi")
    language_rules = (
        "Language: Vietnamese-first, friendly coach tone; keep IELTS/CEFR/Band in English.\n"
        if vi
        else "Language: English, friendly coach tone.\n"
    )
    return (
        "You are an adaptive vocabulary coach. Summarise the learner's day and design the "
        "focus for tomorrow using their captured actions and scores. Be specific and "
        "actionable; reference the actual words and behaviours.\n\n"
        f"Learner CEFR level: {context.get('level') or 'B1'}\n"
        f"Day number: {context.get('day_number') or 1}\n"
        f"Reading score: {context.get('reading_correct') or 0}/{context.get('reading_total') or 0}\n"
        f"Recall score: {context.get('recall_correct') or 0}/{context.get('recall_total') or 0}\n"
        f"Looked-up words: {json.dumps(context.get('looked_up_words') or [], ensure_ascii=False)}\n"
        f"Translated phrases: {json.dumps(context.get('translated_phrases') or [], ensure_ascii=False)}\n"
        f"Selected phrases: {json.dumps(context.get('selected_phrases') or [], ensure_ascii=False)}\n"
        f"Highlighted phrases: {json.dumps(context.get('highlighted_phrases') or [], ensure_ascii=False)}\n"
        f"Explained phrases: {json.dumps(context.get('explained_phrases') or [], ensure_ascii=False)}\n"
        f"Bolded words: {json.dumps(context.get('bolded_words') or [], ensure_ascii=False)}\n"
        f"Wrong answers: {json.dumps(context.get('wrong_answers') or [], ensure_ascii=False)}\n\n"
        f"Grouped reading behaviour: {json.dumps(context.get('action_summary') or {}, ensure_ascii=False)}\n\n"
        f"{language_rules}"
        "Constraints: headline max 60 chars; 3-5 notes max 140 chars each; next_focus max 140; "
        "recommended_words up to 8 lemmas the learner should revisit tomorrow.\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "headline": "string",\n'
        '  "notes": ["string"],\n'
        '  "next_focus": "string",\n'
        '  "recommended_words": ["string"]\n'
        "}\n"
    )


def normalize_coaching_notes_payload(
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    level = str(context.get("level") or "B1")
    looked_up = [str(w) for w in (context.get("looked_up_words") or [])][:8]
    headline = _clamp_text(
        payload.get("headline"),
        max_chars=80,
        fallback=f"Day {context.get('day_number') or 1}: keep your {level} momentum",
    )
    notes = _clean_str_list(
        payload.get("notes"),
        max_items=5,
        max_chars=160,
        fallback=[
            f"Anchor level: {level}. Keep most new words near this level until recall is stable.",
            (
                f"Revisit looked-up words tomorrow: {', '.join(looked_up[:6])}."
                if looked_up
                else "No lookup-heavy word yet; keep the normal daily mix."
            ),
        ],
    )
    next_focus = _clamp_text(
        payload.get("next_focus"),
        max_chars=160,
        fallback="Tomorrow blends recall of today's words with a slightly denser reading.",
    )
    recommended = _clean_str_list(
        payload.get("recommended_words"),
        max_items=8,
        max_chars=48,
        fallback=looked_up,
    )
    return {
        "headline": headline,
        "notes": notes,
        "next_focus": next_focus,
        "recommended_words": recommended,
    }


def build_reading_helper_prompt(*, context: Dict[str, Any]) -> str:
    """Plain-text markdown streamed to the AI Helper — grounded in recent actions only."""
    locale = str(context.get("locale") or "en").lower()
    vi = locale.startswith("vi")
    language = (
        "Write entirely in Vietnamese. Keep IELTS, CEFR, and band labels in English when needed."
        if vi
        else "Write in clear English suitable for an IELTS learner."
    )
    recent = context.get("recent_actions") or []
    has_actions = bool(recent)
    return (
        "You are a live reading coach. The learner is reading an IELTS-style passage. "
        "Respond ONLY to what appears in Recent learner actions (newest last). "
        "Never invent lookups, highlights, bold marks, or translations the learner did not do.\n\n"
        f"{language}\n"
        "Tone: direct and helpful, like a tutor beside them — not meta commentary. "
        "Do NOT use words like tip, tips, mẹo, advice, gợi ý, suggestion, or describe "
        "what the app is doing.\n\n"
        f"Learner CEFR level: {context.get('level') or 'B1'}\n"
        f"Passage title: {json.dumps(context.get('title') or '', ensure_ascii=False)}\n"
        f"Current paragraph: {json.dumps((context.get('paragraph') or '')[:1600], ensure_ascii=False)}\n"
        f"Recent learner actions (chronological, focus on the last 1-3): "
        f"{json.dumps(recent, ensure_ascii=False)}\n\n"
        "If the learner translated text, include the meaning and how it fits the sentence. "
        "If they looked up a word, explain sense in this paragraph. "
        "If they highlighted a phrase, unpack that phrase only.\n\n"
        "Output markdown only. Use exactly these headers (2-3 short bullets each):\n"
        "### Vocabulary focus\n"
        "### Meaning in context\n"
        "### Reading this paragraph\n"
        + (
            "If Recent learner actions is empty, give only a brief neutral read of this "
            "paragraph's main idea and one over-band word to watch — do not claim any learner action.\n"
            if not has_actions
            else "Prioritise the latest action; older actions are background only.\n"
        )
    )


READING_COACH_SYSTEM_PROMPT = """You are Reading Coach for an IELTS Reading app.

The user can do only two actions:
1. Select one word.
2. Select one sentence.

Generate one short coaching note in Vietnamese. Keep important English words/chunks unchanged. Use natural Vietnamese, not dictionary-style lists.

Core principles:
- Be concise and practical. Help the user continue reading.
- Do not translate the whole paragraph.
- Do not give generic advice like "read carefully".
- Focus on the selected text and its sentence/paragraph context.

For WORD selection:
- Prefer the meaningful chunk containing the word, not the word alone when a real chunk exists.
- Explain meaning in this sentence.
- If the sentence has although/however/therefore/because/while/whereas, explain sentence logic briefly.
- Never invent chunks or join random neighboring tokens.
- Never treat "restoration has", "many have", "they are" as vocabulary chunks.
- If the word is low-value, return quick_note or no_note.

For SENTENCE selection:
- Give a one-sentence Vietnamese meaning first.
- Build a sentence map with only labels that exist: Khi nào?, Ai/Cái gì?, Làm gì?, Cách gì?, Để làm gì?, Kết quả/Ý chính?
- If there is a connector, explain the logic pattern.
- Include 2–4 usefulChunks copied from the sentence (exact text).
- Keep it easy to scan.

Return only valid JSON. No markdown. No text outside JSON.
"""

READING_HELPER_NOTE_JSON_SCHEMA = """
{
  "noteType": "word_coach | sentence_coach | quick_note | no_note",
  "priority": 1,
  "shouldShow": true,
  "title": "",
  "targetText": "",
  "meaningVi": "",
  "mainNoteVi": "",
  "bestChunk": {
    "text": "",
    "meaningVi": "",
    "reason": ""
  },
  "sentenceMap": [
    {
      "label": "",
      "text": "",
      "meaningVi": ""
    }
  ],
  "logic": {
    "connector": "",
    "pattern": "",
    "partA": "",
    "partB": "",
    "explanationVi": ""
  },
  "usefulChunks": [
    {
      "text": "",
      "meaningVi": ""
    }
  ],
  "tipVi": "",
  "miniCheckVi": "",
  "avoidShowing": []
}
"""

NOTE_TYPE_TO_CARD_TYPE = {
    "word_coach": "vocab_context",
    "quick_note": "vocab_context",
    "sentence_coach": "phrase_breakdown",
    "no_note": "reading_strategy",
    "quick_vocab": "vocab_context",
    "context_vocab": "vocab_context",
    "sentence_logic": "vocab_context",
    "sentence_breakdown": "phrase_breakdown",
    "paragraph_main_idea": "reading_strategy",
    "question_hint": "evidence_hint",
    "answer_explanation": "question_repair",
}


def _is_v3_reading_coach_raw(raw: Dict[str, Any]) -> bool:
    note_type = str(raw.get("noteType") or raw.get("note_type") or "").strip()
    if note_type in ("word_coach", "sentence_coach", "quick_note"):
        return True
    return bool(str(raw.get("meaningVi") or raw.get("meaning_vi") or "").strip())


def _coerce_v3_reading_coach_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map v3 word/sentence coach JSON into legacy card coercion input."""
    note_type = str(raw.get("noteType") or raw.get("note_type") or "word_coach").strip()
    should_show = raw.get("shouldShow")
    if should_show is None:
        should_show = raw.get("should_show", True)
    if note_type == "no_note":
        should_show = False

    target_text = str(raw.get("targetText") or raw.get("target_text") or "").strip()
    meaning = _strip_coach_guillemets(
        str(raw.get("meaningVi") or raw.get("meaning_vi") or raw.get("meaning") or "")
    )[:220]
    main_note = _strip_coach_guillemets(
        str(
            raw.get("mainNoteVi")
            or raw.get("main_note_vi")
            or raw.get("mainNote")
            or raw.get("main_note")
            or ""
        )
    )[:520]
    tip = _strip_coach_guillemets(str(raw.get("tipVi") or raw.get("tip") or ""))[:300]
    mini_check = _strip_coach_guillemets(
        str(
            raw.get("miniCheckVi")
            or raw.get("miniCheck")
            or raw.get("mini_check")
            or ""
        )
    )[:220]

    logic_raw = raw.get("logic") if isinstance(raw.get("logic"), dict) else {}
    logic = {
        k: _strip_coach_guillemets(str(v))[:420]
        for k, v in logic_raw.items()
        if v not in ("", None)
    }

    chunks_out: List[Dict[str, str]] = []
    for row in (
        raw.get("usefulChunks") or raw.get("useful_chunks") or raw.get("chunks") or []
    ):
        if not isinstance(row, dict):
            continue
        chunk = _strip_coach_guillemets(str(row.get("text") or row.get("chunk") or ""))[
            :220
        ]
        meaning_vi = _strip_coach_guillemets(
            str(row.get("meaningVi") or row.get("meaning_vi") or "")
        )[:320]
        if not chunk:
            continue
        item: Dict[str, str] = {"chunk": chunk}
        if meaning_vi:
            item["meaningVi"] = meaning_vi
        chunks_out.append(item)

    best_raw = raw.get("bestChunk") if isinstance(raw.get("bestChunk"), dict) else {}
    if not best_raw and isinstance(raw.get("best_chunk"), dict):
        best_raw = raw.get("best_chunk")
    best_text = _strip_coach_guillemets(str(best_raw.get("text") or ""))[:220]
    best_gloss = _strip_coach_guillemets(
        str(best_raw.get("meaningVi") or best_raw.get("meaning_vi") or "")
    )[:320]
    if best_text and not any(c.get("chunk") == best_text for c in chunks_out):
        chunks_out.insert(
            0,
            {
                "chunk": best_text,
                **({"meaningVi": best_gloss} if best_gloss else {}),
                **(
                    {
                        "whyUseful": _strip_coach_guillemets(
                            str(best_raw.get("reason") or "")
                        )[:260]
                    }
                    if best_raw.get("reason")
                    else {}
                ),
            },
        )

    sentence_map_out: List[Dict[str, str]] = []
    for row in raw.get("sentenceMap") or raw.get("sentence_map") or []:
        if not isinstance(row, dict):
            continue
        label = _strip_coach_guillemets(str(row.get("label") or ""))[:80]
        text = _strip_coach_guillemets(str(row.get("text") or ""))[:220]
        gloss = _strip_coach_guillemets(
            str(row.get("meaningVi") or row.get("meaning_vi") or "")
        )[:320]
        if not label or not text:
            continue
        sentence_map_out.append(
            {
                "label": label,
                "text": text,
                **({"meaningVi": gloss} if gloss else {}),
            }
        )

    avoid = [
        _strip_coach_guillemets(str(x))[:80]
        for x in (raw.get("avoidShowing") or raw.get("avoid_showing") or [])
        if str(x).strip()
    ][:8]

    try:
        priority_int = int(raw.get("priority") or 3)
    except (TypeError, ValueError):
        priority_int = 3
    priority_int = max(1, min(5, priority_int))

    return {
        "noteType": note_type,
        "priority": priority_int,
        "shouldShow": bool(should_show),
        "title": _strip_coach_guillemets(str(raw.get("title") or ""))[:90],
        "targetText": target_text,
        "meaning": meaning,
        "mainNote": main_note,
        "logic": logic,
        "chunks": chunks_out,
        "tip": tip,
        "miniCheck": mini_check,
        "avoidShowing": avoid,
        "sentenceMap": sentence_map_out,
    }


def _coerce_v2_note_to_card_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map noteType JSON (prompt v2) into legacy card fields + passthrough metadata."""
    note_type = str(
        raw.get("noteType") or raw.get("note_type") or "context_vocab"
    ).strip()
    should_show = raw.get("shouldShow")
    if should_show is None:
        should_show = raw.get("should_show", True)
    if note_type == "no_note":
        should_show = False

    target_text = str(raw.get("targetText") or raw.get("target_text") or "").strip()
    meaning = _strip_coach_guillemets(str(raw.get("meaning") or ""))[:220]
    main_note = _strip_coach_guillemets(
        str(raw.get("mainNote") or raw.get("main_note") or "")
    )[:520]
    tip = _strip_coach_guillemets(str(raw.get("tip") or ""))[:300]
    mini_check = _strip_coach_guillemets(
        str(raw.get("miniCheck") or raw.get("mini_check") or "")
    )[:220]

    logic_raw = raw.get("logic") if isinstance(raw.get("logic"), dict) else {}
    logic = {
        k: _strip_coach_guillemets(str(v))[:420]
        for k, v in logic_raw.items()
        if v not in ("", None)
    }

    chunks_out: List[Dict[str, str]] = []
    for row in raw.get("chunks") or []:
        if not isinstance(row, dict):
            continue
        chunk = _strip_coach_guillemets(str(row.get("chunk") or ""))[:220]
        meaning_vi = _strip_coach_guillemets(
            str(row.get("meaningVi") or row.get("meaning_vi") or "")
        )[:320]
        if not chunk:
            continue
        item: Dict[str, str] = {"chunk": chunk}
        if meaning_vi:
            item["meaningVi"] = meaning_vi
        why = _strip_coach_guillemets(
            str(row.get("whyUseful") or row.get("why_useful") or "")
        )
        if why:
            item["whyUseful"] = why[:260]
        chunks_out.append(item)

    avoid = [
        _strip_coach_guillemets(str(x))[:80]
        for x in (raw.get("avoidShowing") or raw.get("avoid_showing") or [])
        if str(x).strip()
    ][:8]

    target_word = target_text if target_text and " " not in target_text.strip() else ""
    if not target_word and target_text:
        parts = target_text.split()
        target_word = parts[0] if len(parts) == 1 else ""

    sentence_logic: Dict[str, str] = {}
    if logic:
        logic_lines: List[str] = []
        if logic.get("pattern"):
            logic_lines.append(logic["pattern"])
        elif logic.get("connector"):
            logic_lines.append(f"{logic['connector']} A, B")
        if logic.get("partA"):
            logic_lines.append(f"A: {logic['partA']}")
        if logic.get("partB"):
            logic_lines.append(f"B: {logic['partB']}")
        if logic_lines:
            sentence_logic["text"] = "\n".join(logic_lines)[:700]
        if logic.get("explanationVi"):
            exp = logic["explanationVi"]
            sentence_logic["gloss"] = exp if exp.startswith("→") else f"→ {exp}"
    if tip:
        sentence_logic["tip"] = tip

    local_phrase: Dict[str, str] = {}
    if chunks_out:
        local_phrase = {
            "text": chunks_out[0]["chunk"],
            **(
                {"gloss": chunks_out[0]["meaningVi"]}
                if chunks_out[0].get("meaningVi")
                else {}
            ),
        }

    word_gloss: Dict[str, str] = {}
    if meaning:
        word_gloss = {"word": target_word or target_text[:64], "gloss": meaning}

    meaning_in_context: Dict[str, str] = {}
    if main_note and not meaning:
        meaning_in_context["plain"] = main_note

    try:
        priority_int = int(raw.get("priority") or 3)
    except (TypeError, ValueError):
        priority_int = 3
    priority_int = max(1, min(5, priority_int))

    card_type = NOTE_TYPE_TO_CARD_TYPE.get(note_type, "vocab_context")

    return {
        "card_type": card_type,
        "priority": priority_int,
        "should_show": bool(should_show),
        "title": _strip_coach_guillemets(str(raw.get("title") or ""))[:90],
        "target": {
            **({"text": target_text[:400]} if target_text else {}),
            **(
                {"word": (target_word or target_text)[:64]}
                if (target_word or target_text)
                else {}
            ),
        },
        "word_gloss": word_gloss,
        "local_phrase": local_phrase,
        "sentence_logic": sentence_logic,
        "meaning_in_context": meaning_in_context,
        "mini_check": mini_check,
        "reason_for_showing": str(
            raw.get("reasonForShowing") or raw.get("reason_for_showing") or ""
        ).strip()[:220],
        "note_type": note_type,
        "meaning": meaning,
        "main_note": main_note,
        "logic": logic,
        "chunks": chunks_out,
        "avoid_showing": avoid,
    }


def build_reading_helper_note_user_prompt(*, context: Dict[str, Any]) -> str:
    """Word or sentence selection only — compact coach request."""
    sel = context.get("reading_selection") or {}
    selection_type = str(sel.get("selection_type") or "word").strip()
    selected_text = str(sel.get("selected_text") or "").strip()[:1200]
    sentence_text = str(sel.get("sentence_text") or selected_text).strip()[:1200]
    paragraph_text = str(sel.get("paragraph_text") or "").strip()[:2400]
    passage_title = str(sel.get("passage_title") or context.get("title") or "").strip()
    user_level = str(sel.get("user_level") or context.get("level") or "B1").strip()

    return (
        "PASSAGE TITLE:\n"
        f"{json.dumps(passage_title, ensure_ascii=False)}\n\n"
        "USER LEVEL:\n"
        f"{user_level}\n\n"
        "SELECTION TYPE:\n"
        f"{selection_type}\n\n"
        "SELECTED TEXT:\n"
        f"{selected_text}\n\n"
        "SENTENCE CONTAINING SELECTION:\n"
        f"{sentence_text}\n\n"
        "PARAGRAPH CONTEXT:\n"
        f"{paragraph_text}\n\n"
        "TASK:\n"
        "Generate one Reading Coach note.\n"
        "Return JSON with this schema:\n"
        f"{READING_HELPER_NOTE_JSON_SCHEMA}\n"
        "Rules:\n"
        '- If selection_type is "word", fill bestChunk and usefulChunks when useful; sentenceMap may be empty.\n'
        '- If selection_type is "sentence", fill sentenceMap and usefulChunks; bestChunk may be empty.\n'
        "- If there is no connector, keep logic fields empty.\n"
        "- Do not invent chunks.\n"
        "- usefulChunks must be exact text from the sentence.\n"
    )


def build_reading_helper_note_prompt(*, context: Dict[str, Any]) -> str:
    """Single-string prompt (system + user) for backward compatibility."""
    return (
        f"{READING_COACH_SYSTEM_PROMPT}\n\n"
        f"{build_reading_helper_note_user_prompt(context=context)}"
    )


def build_reading_helper_note_messages(
    *, context: Dict[str, Any]
) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": READING_COACH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_reading_helper_note_user_prompt(context=context),
        },
    ]


def _strip_coach_guillemets(value: str) -> str:
    return (value or "").replace("«", "").replace("»", "").strip()


_PLACEHOLDER_SUBSTITUTE_WORDS = {
    "near equivalent",
    "near synonym",
    "substitute",
    "...",
    "…",
}


def normalize_reading_helper_note_payload(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("helper note must be an object")

    v2_passthrough: Dict[str, Any] = {}
    if _is_v3_reading_coach_raw(raw):
        coerced = _coerce_v2_note_to_card_raw(_coerce_v3_reading_coach_raw(raw))
        v2_passthrough = {
            k: coerced[k]
            for k in (
                "note_type",
                "meaning",
                "main_note",
                "logic",
                "chunks",
                "avoid_showing",
                "sentenceMap",
            )
            if coerced.get(k)
        }
        raw = coerced
    elif raw.get("noteType") or raw.get("note_type"):
        coerced = _coerce_v2_note_to_card_raw(raw)
        v2_passthrough = {
            k: coerced[k]
            for k in (
                "note_type",
                "meaning",
                "main_note",
                "logic",
                "chunks",
                "avoid_showing",
            )
            if coerced.get(k)
        }
        raw = coerced
    allowed_types = {
        "vocab_context",
        "phrase_breakdown",
        "logic_bridge",
        "evidence_hint",
        "question_repair",
        "reading_strategy",
        "progress_reflection",
    }
    legacy_type_map = {
        "vocabulary_note": "vocab_context",
        "sentence_breakdown": "phrase_breakdown",
        "logic_note": "logic_bridge",
        "paragraph_main_idea": "reading_strategy",
        "question_hint": "evidence_hint",
        "answer_explanation": "question_repair",
    }

    def _field(*keys: str, limit: int = 600) -> Optional[str]:
        for key in keys:
            val = str(raw.get(key) or "").strip()
            if val:
                return val[:limit]
        return None

    raw_type = str(
        raw.get("card_type")
        or raw.get("cardType")
        or raw.get("noteType")
        or raw.get("note_type")
        or "reading_strategy"
    ).strip()
    card_type = legacy_type_map.get(raw_type, raw_type)
    if card_type not in allowed_types:
        card_type = "reading_strategy"

    target_raw = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    target_text = (
        str(target_raw.get("text") or "").strip()
        or _field("targetText", "target_text", limit=400)
        or _field("phrase", limit=400)
        or ""
    )
    target_word = (
        str(target_raw.get("word") or "").strip()
        or _field("targetWord", "target_word", limit=64)
        or ""
    )
    paragraph_index = target_raw.get("paragraph_index")
    if paragraph_index is None:
        paragraph_index = target_raw.get("paragraphIndex")
    if not isinstance(paragraph_index, int):
        paragraph_index = None

    vocab_out: List[Dict[str, str]] = []
    for row in raw.get("vocab") or []:
        if not isinstance(row, dict):
            continue
        word = str(row.get("word") or "").strip()
        meaning = str(
            row.get("meaning") or row.get("meaningVi") or row.get("meaning_vi") or ""
        ).strip()
        in_context = str(
            row.get("in_context")
            or row.get("inContext")
            or row.get("chunk")
            or row.get("note")
            or ""
        ).strip()
        if not word or not meaning:
            continue
        vocab_out.append(
            {
                "word": word[:64],
                "meaning": meaning[:220],
                "in_context": in_context[:260],
            }
        )

    meaning_raw = (
        raw.get("meaning_in_context")
        if isinstance(raw.get("meaning_in_context"), dict)
        else (
            raw.get("meaningInContext")
            if isinstance(raw.get("meaningInContext"), dict)
            else {}
        )
    )
    meaning_in_context = {
        "plain": _strip_coach_guillemets(
            str(
                meaning_raw.get("plain")
                or raw.get("meaningInContext")
                or raw.get("meaning_in_context")
                or ""
            )
        )[:360],
        "why": _strip_coach_guillemets(
            str(meaning_raw.get("why") or meaning_raw.get("reason") or "")
        )[:360],
        "not_this": _strip_coach_guillemets(
            str(
                meaning_raw.get("not_this")
                or meaning_raw.get("notThis")
                or meaning_raw.get("not_this_meaning")
                or ""
            )
        )[:280],
    }
    meaning_in_context = {
        k: v for k, v in meaning_in_context.items() if v not in ("", None)
    }

    def _gloss_pair(
        raw_key: str, *, text_limit: int = 420, gloss_limit: int = 520
    ) -> Dict[str, str]:
        node = raw.get(raw_key)
        if not isinstance(node, dict):
            return {}
        text = _strip_coach_guillemets(str(node.get("text") or node.get("word") or ""))[
            :text_limit
        ]
        gloss = _strip_coach_guillemets(
            str(node.get("gloss") or node.get("meaning") or node.get("plain") or "")
        )[:gloss_limit]
        out = {
            **({"text": text} if text else {}),
            **({"word": text} if raw_key == "word_gloss" and text else {}),
            **({"gloss": gloss} if gloss else {}),
        }
        return out

    word_gloss = _gloss_pair("word_gloss", text_limit=80, gloss_limit=220)
    local_phrase = _gloss_pair("local_phrase", text_limit=220, gloss_limit=320)
    full_clause = _gloss_pair("full_clause", text_limit=520, gloss_limit=520)

    logic_raw = (
        raw.get("sentence_logic") if isinstance(raw.get("sentence_logic"), dict) else {}
    )
    sentence_logic = {
        "text": _strip_coach_guillemets(str(logic_raw.get("text") or ""))[:700],
        "gloss": _strip_coach_guillemets(
            str(logic_raw.get("gloss") or logic_raw.get("meaning") or "")
        )[:520],
        "tip": _strip_coach_guillemets(str(logic_raw.get("tip") or ""))[:300],
    }
    sentence_logic = {k: v for k, v in sentence_logic.items() if v not in ("", None)}

    context_raw = (
        raw.get("context_clues")
        if isinstance(raw.get("context_clues"), dict)
        else (
            raw.get("contextClues") if isinstance(raw.get("contextClues"), dict) else {}
        )
    )
    context_clues = {
        "before": _strip_coach_guillemets(
            str(context_raw.get("before") or context_raw.get("left") or "")
        )[:220],
        "after": _strip_coach_guillemets(
            str(context_raw.get("after") or context_raw.get("right") or "")
        )[:220],
        "sentence_purpose": _strip_coach_guillemets(
            str(
                context_raw.get("sentence_purpose")
                or context_raw.get("sentencePurpose")
                or context_raw.get("purpose")
                or ""
            )
        )[:360],
        "guess_path": _strip_coach_guillemets(
            str(
                context_raw.get("guess_path")
                or context_raw.get("guessPath")
                or context_raw.get("inference_path")
                or ""
            )
        )[:420],
    }
    context_clues = {k: v for k, v in context_clues.items() if v not in ("", None)}

    substitutes_out: List[Dict[str, Any]] = []
    raw_substitutes = (
        raw.get("substitutes") or raw.get("synonyms") or raw.get("replace_with")
    )
    if isinstance(raw_substitutes, list):
        for item in raw_substitutes[:5]:
            if isinstance(item, dict):
                word = str(
                    item.get("word")
                    or item.get("phrase")
                    or item.get("substitute")
                    or ""
                ).strip()
                reason = str(
                    item.get("reason") or item.get("why") or item.get("note") or ""
                ).strip()
                fits_raw = item.get("fits")
                fits = bool(fits_raw) if isinstance(fits_raw, bool) else None
            else:
                word = str(item or "").strip()
                reason = ""
                fits = None
            if not word or word.lower() in _PLACEHOLDER_SUBSTITUTE_WORDS:
                continue
            substitutes_out.append(
                {
                    "word": word[:80],
                    **({"fits": fits} if fits is not None else {}),
                    **(
                        {"reason": _strip_coach_guillemets(reason)[:260]}
                        if reason
                        else {}
                    ),
                }
            )

    grammar_raw = raw.get("grammar") if isinstance(raw.get("grammar"), dict) else {}
    grammar_out = {
        "pattern": str(
            grammar_raw.get("pattern")
            or grammar_raw.get("structure")
            or raw.get("grammar_pattern")
            or ""
        ).strip()[:260],
        "role": str(grammar_raw.get("role") or raw.get("grammar_role") or "").strip()[
            :260
        ],
        "note": str(
            grammar_raw.get("note")
            or grammar_raw.get("explanation")
            or raw.get("grammar_note")
            or ""
        ).strip()[:360],
    }
    grammar_out = {k: v for k, v in grammar_out.items() if v not in ("", None)}

    collocation_raw = (
        raw.get("collocation") if isinstance(raw.get("collocation"), dict) else {}
    )
    collocation_out = {
        "chunk": str(
            collocation_raw.get("chunk")
            or raw.get("collocation_chunk")
            or target_text
            or ""
        ).strip()[:220],
        "pattern": str(
            collocation_raw.get("pattern") or raw.get("collocation_pattern") or ""
        ).strip()[:260],
    }
    collocation_out = {k: v for k, v in collocation_out.items() if v not in ("", None)}

    diagnosis = _strip_coach_guillemets(
        _field("diagnosis", limit=420)
        or _field("whyVi", "why_vi", "whyEn", "why_en", limit=420)
        or ""
    )
    guide = _strip_coach_guillemets(
        _field("guide", limit=520)
        or _field("readingTip", "reading_tip", "strategyTip", "strategy_tip", limit=320)
        or _field("meaningGloss", "meaning_gloss", limit=220)
        or ""
    )
    concrete_step = _strip_coach_guillemets(
        _field("concrete_step", "concreteStep", limit=260) or ""
    )
    mini_check = _strip_coach_guillemets(
        str(raw.get("mini_check") or raw.get("miniCheck") or "")
    )[:220]

    evidence = raw.get("evidence")
    evidence_out: Dict[str, Any] = {}
    if isinstance(evidence, dict):
        lp = (
            evidence.get("paragraph_index")
            or evidence.get("paragraphIndex")
            or evidence.get("likelyParagraph")
            or evidence.get("likely_paragraph")
        )
        evidence_out = {
            "quote": str(
                evidence.get("quote")
                or evidence.get("questionFocus")
                or evidence.get("question_focus")
                or ""
            ).strip()[:400],
            "why_it_matters": str(
                evidence.get("why_it_matters")
                or evidence.get("whyItMatters")
                or evidence.get("hintVi")
                or evidence.get("hint_vi")
                or ""
            ).strip()[:300],
        }
        if isinstance(lp, int):
            evidence_out["paragraph_index"] = lp
        evidence_out = {k: v for k, v in evidence_out.items() if v not in ("", None)}

    icon = {
        "vocab_context": "word",
        "phrase_breakdown": "phrase",
        "logic_bridge": "logic",
        "evidence_hint": "evidence",
        "question_repair": "repair",
    }.get(card_type, "strategy")
    tone = (
        "urgent"
        if card_type == "question_repair"
        else (
            "quiet"
            if raw.get("priority") is not None
            and str(raw.get("priority")).strip() in {"1", "2"}
            else "normal"
        )
    )

    priority = raw.get("priority")
    try:
        priority_int = int(priority) if priority is not None else 3
    except (TypeError, ValueError):
        priority_int = 3
    priority_int = max(1, min(5, priority_int))
    should_show = raw.get("shouldShow")
    if should_show is None:
        should_show = raw.get("should_show", True)
    trigger_ids = [
        str(x).strip()[:96]
        for x in (raw.get("trigger_event_ids") or raw.get("triggerEventIds") or [])
        if str(x).strip()
    ][:6]
    title = _strip_coach_guillemets(_field("title", limit=90) or "") or {
        "vocab_context": "Word in context",
        "phrase_breakdown": "Phrase breakdown",
        "logic_bridge": "Sentence logic",
        "evidence_hint": "Evidence check",
        "question_repair": "Repair the question",
        "progress_reflection": "Reading pattern",
    }.get(card_type, "Reading coach")

    if card_type == "vocab_context":
        if meaning_in_context.get("plain"):
            diagnosis = ""
            guide = ""
            concrete_step = ""
        generic_vocab_meanings = {
            "nghĩa theo câu này",
            "nghĩa theo ngữ cảnh",
            "meaning in context",
        }
        vocab_out = [
            row
            for row in vocab_out
            if not (
                target_word
                and row.get("word", "").lower() == target_word.lower()
                and row.get("meaning", "").lower() in generic_vocab_meanings
            )
        ]
        wgloss = str(word_gloss.get("gloss") or "").strip().lower()
        if wgloss in _PLACEHOLDER_GLOSSES or wgloss.startswith("cụm quanh"):
            word_gloss.pop("gloss", None)
        lp_gloss = str(local_phrase.get("gloss") or "").strip().lower()
        if lp_gloss.startswith("cụm quanh") or lp_gloss in _PLACEHOLDER_GLOSSES:
            local_phrase.pop("gloss", None)

        sent_for_fix = (
            str(sentence_logic.get("text") or "").split("\n")[0]
            or str(full_clause.get("text") or "")
            or str(evidence_out.get("quote") or "")
        )
        lp_text = str(local_phrase.get("text") or "")
        if sent_for_fix and target_word and lp_text:
            if _chunk_crosses_clause(sent_for_fix, lp_text, target_word):
                clause = _clause_containing_word(sent_for_fix, target_word)
                fixed = _local_phrase_around_word(clause, target_word)
                if fixed:
                    local_phrase["text"] = fixed[:220]

        vague_logic_markers = (
            "giải thích hoặc đối lập",
            "explaining or contrasting",
            "bổ sung thông tin về đối tượng",
        )
        logic_gloss = str(sentence_logic.get("gloss") or "").lower()
        if logic_gloss and any(m in logic_gloss for m in vague_logic_markers):
            sentence_logic.pop("gloss", None)

        if full_clause.get("text") and local_phrase.get("text"):
            if (
                full_clause["text"].lower().strip()
                == local_phrase["text"].lower().strip()
            ):
                full_clause = {}
        vague_fc_gloss = {
            "ý chính của mệnh đề chứa từ vừa click",
            "clause meaning",
        }
        if str(full_clause.get("gloss") or "").lower().strip() in vague_fc_gloss:
            full_clause.pop("gloss", None)

        context_clues = {}
        substitutes_out = []
        grammar_out = {}
        collocation_out = {}

    has_teaching_content = _reading_card_has_substance(
        word_gloss=word_gloss,
        local_phrase=local_phrase,
        full_clause=full_clause,
        sentence_logic=sentence_logic,
        meaning_in_context=meaning_in_context,
        context_clues=context_clues,
        substitutes_out=substitutes_out,
        grammar_out=grammar_out,
        collocation_out=collocation_out,
        diagnosis=diagnosis,
        guide=guide,
        concrete_step=concrete_step,
        vocab_out=vocab_out,
        v2_passthrough=v2_passthrough,
    )
    result = {
        "id": str(raw.get("id") or "").strip()[:96],
        "card_type": card_type,
        "priority": priority_int,
        "trigger_event_ids": trigger_ids,
        "title": title,
        "target": {
            **({"text": target_text[:400]} if target_text else {}),
            **({"word": target_word[:64]} if target_word else {}),
            **(
                {"paragraph_index": paragraph_index}
                if paragraph_index is not None
                else {}
            ),
        },
        "word_gloss": word_gloss,
        "local_phrase": local_phrase,
        "full_clause": full_clause,
        "sentence_logic": sentence_logic,
        "meaning_in_context": meaning_in_context,
        "context_clues": context_clues,
        "substitutes": substitutes_out,
        "grammar": grammar_out,
        "collocation": collocation_out,
        "diagnosis": diagnosis,
        "guide": guide,
        "concrete_step": concrete_step,
        "mini_check": mini_check,
        "vocab": vocab_out[:6],
        "evidence": evidence_out,
        "display": {"tone": tone, "icon": icon},
        "should_show": bool(should_show) and has_teaching_content,
        "reason_for_showing": str(
            raw.get("reason_for_showing") or raw.get("reasonForShowing") or ""
        ).strip()[:220],
        # Legacy fields kept for older clients during rollout.
        "noteType": legacy_type_map.get(card_type, card_type),
        "shouldShow": bool(should_show),
        "keyPoints": [
            x
            for x in (
                meaning_in_context.get("plain") if meaning_in_context else "",
                full_clause.get("gloss") if full_clause else "",
                sentence_logic.get("gloss") if sentence_logic else "",
                context_clues.get("guess_path") if context_clues else "",
                grammar_out.get("note") if grammar_out else "",
                diagnosis,
                guide,
                concrete_step,
            )
            if x
        ][:3],
    }
    result.update(v2_passthrough)
    if v2_passthrough.get("sentenceMap"):
        result["sentence_map"] = v2_passthrough["sentenceMap"]
    return result


_READING_LOGIC_MARKERS = (
    "although",
    "however",
    "therefore",
    "while",
    "whereas",
    "nevertheless",
)
_PREP_FOR_CHUNK = {
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "from",
    "with",
    "into",
    "by",
    "as",
}

_PHRASE_FORWARD_STOP = _PREP_FOR_CHUNK | {
    "a",
    "an",
    "the",
    "these",
    "those",
    "this",
    "that",
    "and",
    "or",
    "but",
    "while",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
}

_STEPWELL_MOCK_GLOSSES_VI: Dict[str, str] = {
    "stepwells": "giếng bậc thang / giếng khơi nước có bậc",
    "spectacular": "ngoạn mục / thật ấn tượng",
    "monuments": "di tích / công trình kỷ niệm",
    "groundwater": "nước ngầm",
    "millennium": "nghìn năm",
    "neglected": "bị bỏ quên / không được chăm sóc",
    "restoration": "việc phục hồi / trùng tu",
    "glory": "vẻ huy hoàng / sự lộng lẫy",
    "utilitarian": "mang tính thực dụng",
    "irrigation": "tưới tiêu",
    "bygone": "đã qua / thuộc thời xa xưa",
    "document": "ghi lại / tường thuật",
}

_STEPWELL_MOCK_GLOSSES_EN: Dict[str, str] = {
    "stepwells": "stepwell / terraced well for drawing water",
    "spectacular": "very impressive to look at",
    "monuments": "important historical structures",
    "groundwater": "water stored underground",
    "millennium": "a thousand years",
    "neglected": "left unmaintained",
    "restoration": "repair and recovery work",
    "glory": "splendour / former greatness",
    "utilitarian": "practical rather than decorative",
    "irrigation": "water supply for crops",
    "bygone": "belonging to an earlier time",
    "document": "record in detail",
}


def _gloss_is_placeholder(value: str) -> bool:
    clean = (value or "").strip().lower()
    if not clean:
        return True
    if clean in _PLACEHOLDER_GLOSSES:
        return True
    return clean.startswith("cụm quanh")


def _reading_card_has_substance(
    *,
    word_gloss: Dict[str, str],
    local_phrase: Dict[str, str],
    full_clause: Dict[str, str],
    sentence_logic: Dict[str, str],
    meaning_in_context: Dict[str, str],
    context_clues: Dict[str, str],
    substitutes_out: List[Dict[str, Any]],
    grammar_out: Dict[str, str],
    collocation_out: Dict[str, str],
    diagnosis: str,
    guide: str,
    concrete_step: str,
    vocab_out: List[Dict[str, str]],
    v2_passthrough: Dict[str, Any],
) -> bool:
    if str(v2_passthrough.get("meaning") or "").strip():
        return True
    if str(v2_passthrough.get("main_note") or "").strip():
        return True
    sentence_map = (
        v2_passthrough.get("sentenceMap") or v2_passthrough.get("sentence_map") or []
    )
    if isinstance(sentence_map, list) and sentence_map:
        return True
    chunks = v2_passthrough.get("chunks") or []
    if isinstance(chunks, list) and chunks:
        return True
    logic_v2 = (
        v2_passthrough.get("logic")
        if isinstance(v2_passthrough.get("logic"), dict)
        else {}
    )
    if any(
        str(logic_v2.get(k) or "").strip()
        for k in ("explanationVi", "partA", "partB", "pattern", "connector")
    ):
        return True

    if word_gloss.get("gloss") and not _gloss_is_placeholder(
        str(word_gloss.get("gloss"))
    ):
        return True
    local_text = str(local_phrase.get("text") or "").strip()
    if local_phrase.get("gloss") and not _gloss_is_placeholder(
        str(local_phrase.get("gloss"))
    ):
        return True
    if local_text and len(local_text.split()) >= 2 and local_phrase.get("gloss"):
        return True
    if full_clause.get("gloss") and not _gloss_is_placeholder(
        str(full_clause.get("gloss"))
    ):
        return True
    if any(str(sentence_logic.get(k) or "").strip() for k in ("gloss", "tip", "text")):
        return True
    if any(
        str(meaning_in_context.get(k) or "").strip()
        for k in ("plain", "why", "not_this")
    ):
        return True
    if context_clues:
        return True
    if substitutes_out or grammar_out or collocation_out or vocab_out:
        return True
    if str(diagnosis or guide or concrete_step).strip():
        return True
    return False


def _norm_reading_token(value: str) -> str:
    return (value or "").strip().lower()


def _sentence_word_tokens(sentence: str) -> List[str]:
    return re.findall(r"[\w'-]+", sentence or "")


_PLACEHOLDER_GLOSSES = {
    "nghĩa theo câu này",
    "nghĩa theo ngữ cảnh",
    "meaning in context",
    "cụm quanh",
}

_PHRASE_BACK_TOKENS = {
    "many",
    "some",
    "several",
    "most",
    "few",
    "have",
    "has",
    "had",
    "been",
    "were",
    "was",
    "are",
    "is",
    "be",
}


def _clause_containing_word(sentence: str, word: str) -> str:
    needle = _norm_reading_token(word)
    for part in re.split(r",\s*", sentence or ""):
        if any(_norm_reading_token(t) == needle for t in _sentence_word_tokens(part)):
            return part.strip()
    return (sentence or "").strip()


def _local_phrase_around_word(clause: str, word: str) -> str:
    tokens = _sentence_word_tokens(clause)
    needle = _norm_reading_token(word)
    idx = next(
        (i for i, t in enumerate(tokens) if _norm_reading_token(t) == needle), -1
    )
    if idx < 0:
        return word

    start_idx = idx
    while start_idx > 0 and idx - start_idx < 4:
        prev = _norm_reading_token(tokens[start_idx - 1])
        if (
            prev in _PHRASE_BACK_TOKENS
            or prev.endswith("ed")
            or prev.endswith("ing")
            or prev in _PREP_FOR_CHUNK
        ):
            start_idx -= 1
            if prev in _PREP_FOR_CHUNK:
                break
        else:
            break

    end_idx = idx
    while end_idx + 1 < len(tokens) and end_idx - idx < 1:
        nxt_raw = tokens[end_idx + 1]
        nxt = _norm_reading_token(nxt_raw)
        if "-" in nxt_raw:
            end_idx += 1
        elif nxt not in _PHRASE_FORWARD_STOP:
            end_idx += 1
        else:
            break

    return " ".join(tokens[start_idx : end_idx + 1])


def _chunk_crosses_clause(sentence: str, chunk: str, word: str) -> bool:
    if not chunk or not sentence:
        return False
    clean = " ".join(chunk.split())
    if "," in clean:
        return True
    parts = [p.strip() for p in re.split(r",\s*", sentence) if p.strip()]
    if len(parts) < 2:
        return False
    needle = _norm_reading_token(word)
    hit = sum(
        1
        for part in parts
        if any(_norm_reading_token(t) == needle for t in _sentence_word_tokens(part))
    )
    if hit != 1:
        return False
    words_in_chunk = {_norm_reading_token(t) for t in _sentence_word_tokens(clean)}
    for part in parts:
        part_words = {_norm_reading_token(t) for t in _sentence_word_tokens(part)}
        if needle in part_words and not words_in_chunk.issubset(part_words):
            return True
    return False


def _build_although_logic(sentence: str, *, vi: bool) -> Dict[str, str]:
    parts = [p.strip() for p in re.split(r",\s*", sentence, maxsplit=1)]
    if len(parts) < 2:
        return {}
    first, second = parts[0], parts[1]
    first_body = re.sub(r"^although\s+", "", first, flags=re.I).strip()
    if vi:
        return {
            "text": f"Although A, B\nA: {first_body}\nB: {second[:220]}",
            "gloss": "→ Dù từng bị bỏ quên, chúng đã được phục hồi lại vẻ huy hoàng trước kia.",
            "tip": "Khi thấy Although, phần sau thường là ý chính.",
        }
    return {
        "text": f"Although A, B\nA: {first_body}\nB: {second[:220]}",
        "gloss": "→ Even though A, B is the main point.",
        "tip": "When you see Although, the part after the comma is usually the main idea.",
    }


def _extract_word_chunk(sentence: str, word: str) -> tuple[str, str]:
    """Return (key_chunk, wider_phrase) within the same clause — never cross commas."""
    clause = _clause_containing_word(sentence, word)
    phrase = _local_phrase_around_word(clause, word)
    if phrase:
        return phrase, phrase
    return word, word


def _last_word_action(recent: List[Dict[str, Any]]) -> Dict[str, Any]:
    for action in reversed(recent):
        if action.get("word"):
            return action
    return {}


def _clean_target_phrase(*, phrase: str, sentence: str, word: str) -> str:
    clean = " ".join((phrase or "").split())
    wnorm = _norm_reading_token(word)
    if clean and clean.lower().count(wnorm) <= 1 and len(clean.split()) <= 8:
        return clean[:160]
    if sentence and word:
        _, wider = _extract_word_chunk(sentence, word)
        return wider[:160]
    return clean[:160] if clean else word


def mock_reading_helper_note(*, context: Dict[str, Any]) -> Dict[str, Any]:
    """Structured card when LLM is unavailable — word or sentence selection only."""
    sel = context.get("reading_selection") or {}
    locale = str(context.get("locale") or "en").lower()
    vi = locale.startswith("vi")
    selected = str(sel.get("selected_text") or "").strip()
    sentence = str(sel.get("sentence_text") or selected).strip()
    selection_type = str(sel.get("selection_type") or "word").strip()

    if selected and selection_type == "sentence":
        return normalize_reading_helper_note_payload(
            {
                "noteType": "sentence_coach",
                "priority": 5,
                "shouldShow": True,
                "title": "Gỡ câu dài" if vi else "Unpack this sentence",
                "targetText": selected[:1200],
                "meaningVi": (
                    "Đọc câu theo ý chính: ai làm gì, khi nào, và để làm gì."
                    if vi
                    else "Read for the main idea: who did what, when, and why."
                ),
                "mainNoteVi": (
                    "Tìm động từ chính trước, rồi gắn các phần bổ sung (thời gian, địa điểm, mục đích)."
                    if vi
                    else "Find the main verb first, then attach time, place, and purpose phrases."
                ),
                "sentenceMap": [],
                "usefulChunks": [],
                "tipVi": (
                    "Với câu dài, đừng dịch từng từ — chia thành các mảnh theo sentence map."
                    if vi
                    else "For long sentences, split into map parts instead of word-by-word translation."
                ),
                "miniCheckVi": (
                    "Động từ chính trong câu này là gì?"
                    if vi
                    else "What is the main verb in this sentence?"
                ),
            }
        )

    if selected and selection_type == "word":
        word = selected.split()[0] if selected.split() else selected
        if word.lower() == "neglected" and "although" in sentence.lower():
            return normalize_reading_helper_note_payload(
                {
                    "noteType": "word_coach",
                    "priority": 5,
                    "shouldShow": True,
                    "title": "Từ trong câu này" if vi else "Word in context",
                    "targetText": word,
                    "meaningVi": (
                        "bị bỏ quên / không được chăm sóc"
                        if vi
                        else "left unmaintained"
                    ),
                    "mainNoteVi": (
                        "Trong câu này, neglected nói rằng nhiều stepwells từng không được quan tâm hoặc bảo tồn."
                        if vi
                        else "Here, neglected means many stepwells were once left unmaintained."
                    ),
                    "bestChunk": {
                        "text": "many have been neglected",
                        "meaningVi": (
                            "nhiều stepwells từng bị bỏ quên"
                            if vi
                            else "many were once neglected"
                        ),
                        "reason": (
                            "Đây là cụm thật chứa từ neglected."
                            if vi
                            else "Real chunk with neglected."
                        ),
                    },
                    "logic": {
                        "connector": "Although",
                        "pattern": "Although A, B",
                        "partA": "many have been neglected",
                        "partB": "recent restoration has returned them to their former glory",
                        "explanationVi": (
                            "Dù nhiều stepwells từng bị bỏ quên, việc phục hồi gần đây đã giúp chúng lấy lại vẻ huy hoàng. Phần sau là ý chính."
                            if vi
                            else "Although many were neglected, recent restoration returned their glory. The second part is the main point."
                        ),
                    },
                    "usefulChunks": [
                        {
                            "text": "many have been neglected",
                            "meaningVi": (
                                "nhiều stepwells từng bị bỏ quên"
                                if vi
                                else "many were once neglected"
                            ),
                        },
                        {
                            "text": "returned them to their former glory",
                            "meaningVi": (
                                "lấy lại vẻ huy hoàng"
                                if vi
                                else "returned to former glory"
                            ),
                        },
                    ],
                    "tipVi": (
                        "Khi thấy Although, chia câu thành 2 phần. Phần sau thường là ý chính."
                        if vi
                        else "With Although, split the sentence in two; the second part is often the main idea."
                    ),
                    "miniCheckVi": (
                        "Câu này đối lập giữa bị bỏ quên và điều gì?"
                        if vi
                        else "What contrast does this sentence set up?"
                    ),
                    "avoidShowing": ["neglected recent", "restoration has"],
                }
            )
        return normalize_reading_helper_note_payload(
            {
                "noteType": "word_coach",
                "priority": 4,
                "shouldShow": True,
                "title": "Từ trong câu này" if vi else "Word in context",
                "targetText": word,
                "meaningVi": "nghĩa theo ngữ cảnh" if vi else "meaning in context",
                "mainNoteVi": (
                    f"Giải thích «{word}» trong câu đang đọc, ưu tiên cụm có nghĩa thật quanh từ."
                    if vi
                    else f"Explain «{word}» in this sentence using a real chunk when possible."
                ),
            }
        )

    recent = context.get("recent_actions") or []
    reading_state = context.get("reading_state") or {}
    if not recent:
        return normalize_reading_helper_note_payload(
            {
                "card_type": "reading_strategy",
                "priority": 1,
                "title": "Reading Coach",
                "diagnosis": "",
                "guide": "",
                "concrete_step": "",
                "should_show": False,
                "reason_for_showing": "no recent actions",
            }
        )

    difficulty = str(reading_state.get("likelyDifficulty") or "unknown")
    last = recent[-1] if isinstance(recent[-1], dict) else {}
    last_word = _last_word_action(recent)
    word = str(last.get("word") or last_word.get("word") or "").strip()
    sentence = str(last.get("sentence") or last_word.get("sentence") or "").strip()
    phrase = _clean_target_phrase(
        phrase=str(last.get("phrase") or last_word.get("phrase") or ""),
        sentence=sentence,
        word=word,
    )
    key_chunk, wider_phrase = (
        _extract_word_chunk(sentence, word) if sentence and word else (phrase, phrase)
    )
    if not key_chunk and phrase:
        key_chunk = phrase
    if not wider_phrase and phrase:
        wider_phrase = phrase

    joined = " ".join(
        [
            phrase,
            key_chunk,
            wider_phrase,
            word,
            " ".join(str(a.get("phrase") or "") for a in recent),
            " ".join(str(a.get("word") or "") for a in recent),
        ]
    ).lower()

    event_type = str(last.get("event_type") or last.get("type") or "")
    trigger_ids = [
        str(a.get("event_id") or "").strip()
        for a in recent[-3:]
        if isinstance(a, dict) and str(a.get("event_id") or "").strip()
    ]

    if (
        difficulty == "question_evidence"
        or (event_type == "reading_answer" and last.get("is_correct") is False)
        or any(
            (a.get("event_type") or a.get("type")) == "reading_answer"
            and a.get("is_correct") is False
            for a in recent
            if isinstance(a, dict)
        )
    ):
        return normalize_reading_helper_note_payload(
            {
                "card_type": "question_repair",
                "priority": 5,
                "trigger_event_ids": trigger_ids,
                "title": "Sửa bằng evidence",
                "target": {"text": phrase or sentence[:160]},
                "diagnosis": "Bạn vừa chọn sai hoặc đang thiếu evidence rõ ràng cho câu hỏi.",
                "guide": "Quay lại câu có cùng ý với prompt và chỉ giữ thông tin passage nói trực tiếp.",
                "concrete_step": "Highlight một cụm 3-8 từ làm bằng chứng trước khi chọn tiếp.",
                "mini_check": "Đáp án được nêu trực tiếp hay phải suy ra từ câu nào?",
                "vocab": [],
                "display": {"tone": "urgent", "icon": "repair"},
                "should_show": True,
                "reason_for_showing": "wrong reading answer",
            }
        )

    if (
        any(m in joined for m in _READING_LOGIC_MARKERS) or difficulty == "logic"
    ) and not (
        word and event_type in ("word_click", "word_lookup", "lookup", "translate")
    ):
        return normalize_reading_helper_note_payload(
            {
                "card_type": "logic_bridge",
                "priority": 5,
                "trigger_event_ids": trigger_ids,
                "title": "Logic của câu",
                "target": {"text": phrase or sentence[:160], "word": word or None},
                "diagnosis": "Bạn đang chạm vào một đoạn có quan hệ logic như tương phản, nguyên nhân hoặc phạm vi.",
                "guide": "Đọc hai vế quanh connector: vế nào là ý chính, vế nào chỉ bổ sung điều kiện hoặc đối lập.",
                "concrete_step": "Viết lại quan hệ bằng một mũi tên ngắn: A -> vì/đối lập/dẫn tới -> B.",
                "mini_check": "Connector đang nối hai ý theo quan hệ gì?",
                "vocab": (
                    [
                        {
                            "word": word,
                            "meaning": "nghĩa theo ngữ cảnh",
                            "in_context": key_chunk or word,
                        }
                    ]
                    if word
                    else []
                ),
                "display": {"tone": "normal", "icon": "logic"},
                "should_show": True,
                "reason_for_showing": "logic connector in learner action",
            }
        )

    if word and event_type in ("word_click", "word_lookup", "lookup", "translate"):
        locale = str(context.get("locale") or "en").lower()
        vi = locale.startswith("vi")
        clause = _clause_containing_word(sentence, word) if sentence else ""
        local_chunk = (
            _local_phrase_around_word(clause, word) if clause else (key_chunk or word)
        )
        chunk = local_chunk or key_chunk or phrase or word
        sentence_lower = sentence.lower()

        if (
            word.lower() == "neglected"
            and "although" in sentence_lower
            and "," in sentence
        ):
            return normalize_reading_helper_note_payload(
                {
                    "noteType": "sentence_logic",
                    "priority": 5,
                    "shouldShow": True,
                    "title": "Từ trong câu này" if vi else "Word in context",
                    "targetText": word,
                    "meaning": (
                        "bị bỏ quên / không được chăm sóc"
                        if vi
                        else "left unmaintained / not cared for"
                    ),
                    "mainNote": (
                        "Trong câu này, neglected nói rằng nhiều stepwells từng bị bỏ quên hoặc không được bảo tồn."
                        if vi
                        else "Here, neglected means many stepwells were once left unmaintained."
                    ),
                    "logic": {
                        "connector": "Although",
                        "pattern": "Although A, B",
                        "partA": "many have been neglected",
                        "partB": "recent restoration has returned them to their former glory",
                        "explanationVi": (
                            "Dù nhiều stepwells từng bị bỏ quên, việc phục hồi gần đây đã giúp chúng lấy lại vẻ huy hoàng trước kia. Phần sau là ý chính."
                            if vi
                            else "Even though many were neglected, recent restoration returned them to their former glory. The second part is the main point."
                        ),
                    },
                    "chunks": [
                        {
                            "chunk": "many have been neglected",
                            "meaningVi": (
                                "nhiều stepwells từng bị bỏ quên"
                                if vi
                                else "many stepwells were once neglected"
                            ),
                            "whyUseful": (
                                "Đây là cụm thật chứa từ neglected."
                                if vi
                                else "Real chunk containing neglected."
                            ),
                        },
                        {
                            "chunk": "returned them to their former glory",
                            "meaningVi": (
                                "giúp chúng lấy lại vẻ huy hoàng trước kia"
                                if vi
                                else "returned them to their former splendour"
                            ),
                            "whyUseful": (
                                "Cụm này cho thấy kết quả của restoration."
                                if vi
                                else "Shows the result of restoration."
                            ),
                        },
                    ],
                    "tip": (
                        "Khi thấy Although, chia câu thành 2 phần. Phần sau thường là ý tác giả muốn nhấn mạnh."
                        if vi
                        else "When you see Although, split the sentence in two. The part after the comma is usually the main idea."
                    ),
                    "miniCheck": (
                        "Câu này đang đối lập giữa việc bị bỏ quên và điều gì?"
                        if vi
                        else "What does this sentence contrast neglect with?"
                    ),
                    "avoidShowing": ["neglected recent", "restoration has"],
                    "reasonForShowing": "User clicked a word inside an important contrast sentence.",
                }
            )

        word_gloss_text = {
            "glory": "vẻ huy hoàng / sự lộng lẫy" if vi else "splendour / greatness",
            "restoration": (
                "việc phục hồi / trùng tu" if vi else "restoration / repair work"
            ),
            "irrigation": "tưới tiêu" if vi else "irrigation",
        }.get(word.lower()) or (
            _STEPWELL_MOCK_GLOSSES_VI if vi else _STEPWELL_MOCK_GLOSSES_EN
        ).get(
            word.lower()
        )

        logic_tip = ""
        although_logic: Dict[str, str] = {}
        if "although" in sentence_lower and "," in sentence:
            although_logic = _build_although_logic(sentence, vi=vi)
            logic_tip = although_logic.get("tip", "")
        elif "however" in sentence_lower:
            logic_tip = (
                "Khi thấy However, phần sau thường đổi hướng hoặc đối lập với ý trước."
                if vi
                else "After However, the next clause often contrasts with what came before."
            )
        elif "therefore" in sentence_lower:
            logic_tip = (
                "Khi thấy Therefore, phần sau thường là kết quả/kết luận."
                if vi
                else "After Therefore, the next clause is usually the result or conclusion."
            )

        local_gloss = ""

        has_gloss = bool(word_gloss_text)
        has_chunk = chunk.lower() != word.lower() and len(chunk.split()) >= 2
        has_logic = bool(although_logic) or bool(logic_tip)

        if not has_gloss and not has_chunk and not has_logic:
            return normalize_reading_helper_note_payload(
                {
                    "noteType": "no_note",
                    "priority": 1,
                    "shouldShow": False,
                    "title": "",
                    "targetText": word,
                    "reasonForShowing": "low-value word click without enough context",
                }
            )

        payload: Dict[str, Any] = {
            "card_type": "vocab_context",
            "priority": 4,
            "trigger_event_ids": trigger_ids,
            "title": "Từ trong câu này" if vi else "Word in context",
            "target": {"word": word},
            "word_gloss": {
                "word": word,
                **({"gloss": word_gloss_text} if word_gloss_text else {}),
            },
            "local_phrase": (
                {
                    "text": chunk,
                    **({"gloss": local_gloss} if local_gloss else {}),
                }
                if chunk.lower() != word.lower()
                else {}
            ),
            "sentence_logic": (
                although_logic
                if although_logic
                else (
                    {
                        **({"tip": logic_tip} if logic_tip else {}),
                    }
                    if logic_tip
                    else {}
                )
            ),
            "should_show": True,
            "reason_for_showing": "word interaction with context chunk",
        }
        return normalize_reading_helper_note_payload(payload)

    target = phrase or wider_phrase or sentence
    if target and (
        len(target) > 40
        or event_type
        in ("text_select", "highlight", "highlight_add", "explain", "explain_request")
    ):
        return normalize_reading_helper_note_payload(
            {
                "card_type": (
                    "evidence_hint" if "highlight" in event_type else "phrase_breakdown"
                ),
                "priority": 4,
                "trigger_event_ids": trigger_ids,
                "title": "Gỡ cụm đang đọc",
                "target": {"text": target[:400]},
                "diagnosis": "Bạn đang dừng lại ở một cụm/câu dài, có thể vì cấu trúc hoặc evidence chưa rõ.",
                "guide": "Tách cụm này thành chủ thể, hành động chính và phần bổ sung; đừng dịch từng từ một.",
                "concrete_step": "Gạch dưới động từ chính hoặc cụm evidence ngắn nhất trong câu.",
                "mini_check": "Ai/điều gì đang làm gì trong cụm này?",
                "vocab": [],
                "display": {
                    "tone": "normal",
                    "icon": "evidence" if "highlight" in event_type else "phrase",
                },
                "should_show": True,
                "reason_for_showing": "phrase interaction",
            }
        )

    return normalize_reading_helper_note_payload(
        {
            "card_type": "reading_strategy",
            "priority": 1,
            "title": "",
            "diagnosis": "",
            "guide": "",
            "concrete_step": "",
            "vocab": [],
            "should_show": False,
            "reason_for_showing": "weak signals",
        }
    )


_WORD_COACH_NOTE_TYPES = frozenset(
    {"word_coach", "context_vocab", "quick_vocab", "quick_note"}
)
_SENTENCE_COACH_NOTE_TYPES = frozenset({"sentence_coach", "sentence_breakdown"})
_WORD_COACH_CARD_TYPES = frozenset({"vocab_context"})
_SENTENCE_COACH_CARD_TYPES = frozenset({"phrase_breakdown"})


def reading_coach_card_kind(card: Dict[str, Any]) -> str:
    """Classify a normalized coach card as word, sentence, or other."""
    card_type = str(card.get("card_type") or "").strip()
    note_type = str(card.get("note_type") or "").strip()
    if (
        card_type in _SENTENCE_COACH_CARD_TYPES
        or note_type in _SENTENCE_COACH_NOTE_TYPES
    ):
        return "sentence"
    target_text = str((card.get("target") or {}).get("text") or "").strip()
    if note_type == "sentence_logic" and len(target_text.split()) > 2:
        return "sentence"
    if card_type in _WORD_COACH_CARD_TYPES and (
        note_type in _WORD_COACH_NOTE_TYPES or not note_type
    ):
        return "word"
    if card_type in _WORD_COACH_CARD_TYPES:
        return "word"
    return "other"


def align_reading_coach_card_to_selection(
    card: Dict[str, Any],
    *,
    reading_selection: Optional[Dict[str, Any]] = None,
    locale: str = "en",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ensure card type matches word vs sentence selection intent."""
    sel = reading_selection or {}
    selection_type = str(sel.get("selection_type") or "word").strip().lower()
    if selection_type not in ("word", "sentence"):
        selection_type = "word"

    kind = reading_coach_card_kind(card)
    if selection_type == "word" and kind == "sentence":
        ctx = dict(context or {})
        ctx.setdefault("locale", locale)
        ctx["reading_selection"] = sel
        return mock_reading_helper_note(context=ctx)
    if selection_type == "sentence" and kind == "word":
        ctx = dict(context or {})
        ctx.setdefault("locale", locale)
        ctx["reading_selection"] = sel
        return mock_reading_helper_note(context=ctx)
    return card


def mock_reading_helper_text(*, context: Dict[str, Any]) -> str:
    locale = str(context.get("locale") or "en").lower()
    vi = locale.startswith("vi")
    recent = context.get("recent_actions") or []
    if not recent:
        if vi:
            return (
                "### Từ vựng trọng tâm\n"
                "- Đọc đoạn này để nắm chủ đề chính trước khi tra từ.\n"
                "### Nghĩa trong ngữ cảnh\n"
                "- Chú ý các từ chỉ thời gian, quy mô và mức độ — chúng thường mang ý logic của đoạn.\n"
                "### Đọc đoạn này\n"
                "- Double-click một từ khó hoặc bôi chọn cụm để nhận giải thích ngay tại đây.\n"
            )
        return (
            "### Vocabulary focus\n"
            "- Skim this paragraph for the main topic before looking up words.\n"
            "### Meaning in context\n"
            "- Watch time, scale, and degree words — they often carry the logic of the passage.\n"
            "### Reading this paragraph\n"
            "- Double-click a difficult word or select a phrase to get help here.\n"
        )

    last = recent[-3:]
    vocab_lines: List[str] = []
    context_lines: List[str] = []
    read_lines: List[str] = []

    for action in last:
        et = str(action.get("type") or "")
        if et == "lookup" and action.get("word"):
            w = action["word"]
            if vi:
                vocab_lines.append(
                    f"- **{w}**: đọc trong câu gốc, đoán nghĩa rồi đối chiếu định nghĩa vừa tra."
                )
            else:
                vocab_lines.append(
                    f"- **{w}**: read it in the host sentence, guess the sense, then match the definition you opened."
                )
        elif et == "translate":
            src = str(action.get("phrase") or "")[:80]
            tr = str(action.get("translation") or "")[:120]
            if vi:
                context_lines.append(
                    f"- \"{src}\" → {tr or '…'}: nghĩa trong câu này, không tách rời ngữ cảnh."
                )
            else:
                context_lines.append(
                    f"- \"{src}\" → {tr or '…'}: keep the sense tied to this sentence, not isolated."
                )
        elif et == "highlight" and action.get("phrase"):
            ph = str(action["phrase"])[:80]
            if vi:
                context_lines.append(
                    f'- Cụm **"{ph}"**: paraphrase bằng tiếng Việt một câu, giữ vai trò trong đoạn.'
                )
            else:
                context_lines.append(
                    f'- Phrase **"{ph}"**: paraphrase in one line, keeping its role in the paragraph.'
                )
        elif et == "bold" and action.get("word"):
            w = action["word"]
            if vi:
                vocab_lines.append(
                    f"- **{w}**: bạn đang tập trung vào từ này — nghĩa cốt lõi trong đoạn là gì?"
                )
            else:
                vocab_lines.append(
                    f"- **{w}**: you're focusing on this word — what is its core sense here?"
                )
        elif et == "explain" and action.get("phrase"):
            ph = str(action["phrase"])[:80]
            if vi:
                read_lines.append(
                    f'- Cụm **"{ph}"**: nối ý câu trước và sau để thấy vì sao tác giả dùng cụm này.'
                )
            else:
                read_lines.append(
                    f'- **"{ph}"**: link the sentence before and after to see why the author used it.'
                )

    def _section(title: str, lines: List[str], fallback: str) -> str:
        body = "\n".join(lines[-3:]) if lines else f"- {fallback}"
        return f"{title}\n{body}\n"

    if vi:
        return (
            _section(
                "### Từ vựng trọng tâm",
                vocab_lines,
                "Tiếp tục với từ bạn vừa tương tác.",
            )
            + _section(
                "### Nghĩa trong ngữ cảnh",
                context_lines,
                "Dịch hoặc tra từ trong câu để hiểu sâu hơn.",
            )
            + _section(
                "### Đọc đoạn này",
                read_lines,
                "Đọc câu chứa từ vừa chọn trước khi sang câu tiếp theo.",
            )
        )
    return (
        _section(
            "### Vocabulary focus",
            vocab_lines,
            "Stay with the word you just interacted with.",
        )
        + _section(
            "### Meaning in context",
            context_lines,
            "Translate or look up words inside their sentence.",
        )
        + _section(
            "### Reading this paragraph",
            read_lines,
            "Re-read the sentence with your latest word before moving on.",
        )
    )
