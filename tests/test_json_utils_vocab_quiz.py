"""Unit tests for vocab quiz AI feedback normalization."""

from aiforen.integrations.llm.json_utils import (
    build_vocab_quiz_eval_prompt,
    extract_json,
    normalize_vocab_quiz_ai_feedback,
)


def test_build_vocab_quiz_eval_prompt_includes_score_fields():
    prompt = build_vocab_quiz_eval_prompt(
        task_type="translate_with_hints",
        prompt="Translate to English",
        context="Công viên gần nhà.",
        learner_answer="The park is near my house.",
        target_word="near",
        model_answer="The park is close to my home.",
    )
    assert "score_explanation" in prompt
    assert "score_breakdown" in prompt
    assert "translate_with_hints" in prompt or "meaning" in prompt


def test_normalize_passes_score_explanation_and_breakdown():
    result = normalize_vocab_quiz_ai_feedback(
        {
            "status": "ok",
            "score": 4,
            "passed": True,
            "recommendation": "Cụm 'near my house' hơi cứng. Thử 'close to my home' vì tự nhiên hơn.",
            "score_explanation": "4/5 vì collocation chưa tự nhiên.",
            "score_breakdown": [
                {"criterion": "naturalness", "points": 3, "note": "Word choice stiff."},
            ],
            "corrected_sentence": "The park is close to my home.",
        },
        learner_answer="The park is near my house.",
        model_answer="The park is close to my home.",
        task_type="translate",
        ai_scoring={"max_score": 5, "pass_score": 4},
    )
    assert result["score"] == 4
    assert result["passed"] is True
    assert "score_explanation" in result
    assert result["score_breakdown"][0]["criterion"] == "naturalness"


def test_normalize_synthesizes_fallback_when_generic_recommendation():
    result = normalize_vocab_quiz_ai_feedback(
        {
            "status": "ok",
            "score": 4,
            "passed": True,
            "recommendation": "Gần được!",
            "corrected_sentence": "The park is close to my home.",
        },
        learner_answer="The park is near my house.",
        model_answer="The park is close to my home.",
        ai_scoring={"max_score": 5, "pass_score": 4},
    )
    assert result["score"] == 4
    assert result.get("score_explanation")
    assert result["recommendation"]
    assert "gần được" not in result["recommendation"].lower()


def test_normalize_clears_explanation_at_max_score():
    result = normalize_vocab_quiz_ai_feedback(
        {
            "status": "ok",
            "score": 5,
            "passed": True,
            "recommendation": "Great!",
            "score_explanation": "should be dropped",
            "score_breakdown": [{"criterion": "x", "points": 1, "note": "y"}],
            "corrected_sentence": "The park is near my house.",
        },
        learner_answer="The park is near my house.",
        model_answer="The park is close to my home.",
        ai_scoring={"max_score": 5, "pass_score": 4},
    )
    assert result["score"] == 5
    assert "score_explanation" not in result
    assert "score_breakdown" not in result


def test_extract_json_parses_markdown_fenced_payload():
    raw = '```json\n{"status": "ok", "score": 4}\n```'
    assert extract_json(raw) == {"status": "ok", "score": 4}


def test_extract_json_extracts_object_from_prose():
    raw = 'Here is the result:\n{"status": "ok", "passed": true}\nThanks!'
    assert extract_json(raw)["status"] == "ok"
    assert extract_json(raw)["passed"] is True


def test_extract_json_repairs_trailing_commas():
    raw = '{"status": "ok", "score": 4,}'
    assert extract_json(raw) == {"status": "ok", "score": 4}
