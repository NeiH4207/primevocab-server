"""Anthropic-backed writing evaluator.

Uses Claude to produce IELTS-style structured feedback, then emits the same
streaming event contract expected by the frontend.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict

from anthropic import AsyncAnthropic

from aiforen.core.config import get_settings

from .base import EvaluationStreamEvent, LLMProvider
from .json_utils import (
    build_coaching_notes_prompt,
    build_reading_explain_prompt,
    build_reading_questions_prompt,
    build_vocab_calibration_prompt,
    build_vocab_daily_mission_prompt,
    build_vocab_eval_prompt,
    build_vocab_quiz_eval_prompt,
    extract_json,
    normalize_coaching_notes_payload,
    normalize_reading_explain_payload,
    normalize_reading_questions_payload,
    normalize_vocab_calibration_payload,
    normalize_vocab_daily_mission_payload,
    normalize_vocab_eval_payload,
    normalize_vocab_quiz_ai_feedback,
    normalize_writing_assessment,
)
from .retry import (
    anthropic_messages_with_model_fallback,
    anthropic_messages_with_retry,
    vocab_eval_model_chain,
)


class AnthropicLLMProvider(LLMProvider):
    async def generate_vocab_calibration_review(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_vocab_calibration_prompt(context=context)
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=900,
            temperature=0.25,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return normalize_vocab_calibration_payload(extract_json(text), context=context)

    async def generate_vocab_daily_mission(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_vocab_daily_mission_prompt(context=context)
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=1100,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return normalize_vocab_daily_mission_payload(
            extract_json(text),
            context=context,
        )

    async def explain_reading_phrase(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_reading_explain_prompt(context=context)
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=500,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return normalize_reading_explain_payload(
            extract_json(text),
            phrase=str(context.get("phrase") or ""),
            sentence=str(context.get("sentence") or ""),
            level=str(context.get("level") or "B1"),
        )

    async def generate_reading_questions(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_reading_questions_prompt(context=context)
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=1300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return normalize_reading_questions_payload(
            extract_json(text),
            count=int(context.get("count") or 4),
            fallback_questions=context.get("fallback_questions"),
        )

    async def generate_coaching_notes(
        self,
        *,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_coaching_notes_prompt(context=context)
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=700,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return normalize_coaching_notes_payload(extract_json(text), context=context)

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
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = build_vocab_eval_prompt(
            word=word,
            translate_prompt=translate_prompt,
            topic_prompt=topic_prompt,
            translate_sentence=translate_sentence,
            topic_sentence=topic_sentence,
            current_band=current_band,
            target_band=target_band,
            weakness_context=weakness_context,
        )
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=900,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        payload = extract_json(text)
        return normalize_vocab_eval_payload(
            payload,
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
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        eval_prompt = build_vocab_quiz_eval_prompt(
            task_type=task_type,
            prompt=prompt,
            context=context,
            learner_answer=learner_answer,
            target_word=target_word,
            model_answer=model_answer,
            source_sentence=source_sentence,
            rubric=rubric,
            accepted_flexibility=accepted_flexibility,
            ai_scoring=ai_scoring,
        )
        primary = settings.anthropic_vocab_eval_model or settings.anthropic_model
        models = vocab_eval_model_chain(primary)
        resp, _used_model = await anthropic_messages_with_model_fallback(
            client,
            models=models,
            max_tokens=700,
            temperature=0.1,
            messages=[{"role": "user", "content": eval_prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        payload = extract_json(text)
        return normalize_vocab_quiz_ai_feedback(
            payload,
            learner_answer=learner_answer,
            model_answer=model_answer,
            task_type=task_type,
            ai_scoring=ai_scoring,
        )

    async def evaluate_writing(
        self, *, task: Dict[str, Any], answer: str
    ) -> AsyncIterator[EvaluationStreamEvent]:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        yield EvaluationStreamEvent(
            status="processing",
            step="task_achievement",
            message="Sending essay to Claude...",
        )

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = (
            "You are an IELTS writing examiner. Evaluate this response and return ONLY valid JSON.\n"
            "Use this exact schema:\n"
            "{\n"
            '  "task_achievement": {"score": number, "feedback": string},\n'
            '  "coherence_cohesion": {"score": number, "feedback": string},\n'
            '  "lexical_resource": {"score": number, "feedback": string},\n'
            '  "grammar_accuracy": {"score": number, "feedback": string},\n'
            '  "scores": {\n'
            '    "task_achievement": number,\n'
            '    "coherence_cohesion": number,\n'
            '    "lexical_resource": number,\n'
            '    "grammar_accuracy": number,\n'
            '    "overall_score": number\n'
            "  },\n"
            '  "general_comments": string,\n'
            '  "improvement_suggestions": string,\n'
            '  "improvement_explanation": string,\n'
            '  "next_level_sample": string\n'
            "}\n"
            "Use IELTS band range 0-9 with one decimal when useful.\n\n"
            f"Task Title: {task.get('title', '')}\n"
            f"Task Type: {task.get('task_type', '')}\n"
            f"Task Description: {task.get('description', '')}\n\n"
            f"Student Answer:\n{answer}\n"
        )

        resp = await anthropic_messages_with_retry(
            client,
            model=settings.anthropic_model,
            max_tokens=1800,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        assessment = normalize_writing_assessment(extract_json(text))

        for step in (
            "task_achievement",
            "coherence_cohesion",
            "lexical_resource",
            "grammar_accuracy",
        ):
            yield EvaluationStreamEvent(
                status="completed", step=step, content=assessment[step]
            )

        yield EvaluationStreamEvent(
            status="completed",
            step="general_comments",
            content=assessment["general_comments"],
        )
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
            status="completed",
            step="next_level_sample",
            content=assessment["next_level_sample"],
        )
        yield EvaluationStreamEvent(status="completed", step="final", data=assessment)
