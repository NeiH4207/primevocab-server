"""Unit tests for the vocab coaching engine (pure logic + mock LLM)."""

import asyncio

from aiforen.domain.reading_coach_cache import (
    READING_COACH_PROMPT_VERSION,
    build_reading_coach_cache_key,
    cache_key_from_selection,
    is_cacheable_reading_coach_card,
    normalize_sentence_text,
)
from aiforen.domain.vocab_coaching_reading import (
    CURATED_DIFFICULT_WORDS,
    build_reading_payload,
    find_sentence,
    passage_text,
    passage_tokens,
)
from aiforen.domain.vocab_coaching_readings_data import (
    READING_DAY_COUNT,
    get_reading_seed,
)
from aiforen.integrations.llm.json_utils import (
    build_reading_questions_prompt,
    normalize_coaching_notes_payload,
    normalize_reading_explain_payload,
    normalize_reading_questions_payload,
)
from aiforen.integrations.llm.mock import MockLLMProvider
from aiforen.services.vocab_coaching_service import (
    CEFR_LEVELS,
    TOTAL_DAYS,
    VocabCoachingService,
    _cefr_offset,
    _coaching_word_meets_plan_level,
    _confidence_pct,
    _ielts_band,
    _reading_vocab_candidates_stale,
)


# --------------------------------------------------------------- reading seed
def test_passage_tokens_non_empty_and_unique():
    tokens = passage_tokens()
    assert len(tokens) > 50
    assert len(tokens) == len(set(tokens))
    assert "stepwells" in tokens


def test_find_sentence_locates_phrase():
    sentence = find_sentence("former glory")
    assert "former glory" in sentence.lower()


def test_build_reading_payload_shape():
    difficult = [{"word": "aquifer", "cefr": "C1", "band": 7.5, "in_db": False}]
    payload = build_reading_payload(difficult, day_number=1)
    assert payload["id"] == "cambridge10-test1-passage1"
    assert len(payload["paragraphs"]) >= 10
    assert payload["difficult_words"] == difficult
    assert len(payload["questions"]) >= 4
    for question in payload["questions"]:
        assert question["correct_option"] in question["options"]

    day_two = build_reading_payload([], day_number=2)
    assert day_two["id"] == "cambridge10-test1-passage2"
    assert len(day_two["paragraphs"]) >= 5
    assert day_two["questions"] == []


def test_curated_words_present_in_passage():
    text = passage_text().lower()
    # A representative sample of curated over-band words appears in the passage.
    for word in ("utilitarian", "intricate", "pristine", "aquifer"):
        assert word in CURATED_DIFFICULT_WORDS
        assert word in text


# ------------------------------------------------------------ service helpers
def test_coaching_word_meets_plan_level_for_b2():
    assert _coaching_word_meets_plan_level("B1", "B2") is True
    assert _coaching_word_meets_plan_level("B2", "B2") is True
    assert _coaching_word_meets_plan_level("C1", "B2") is True
    assert _coaching_word_meets_plan_level("A1", "B2") is False
    assert _coaching_word_meets_plan_level("A2", "B2") is False


def test_reading_vocab_candidates_stale_detects_a1_for_b2():
    stale = [{"word": "city", "cefr": "A1", "quiz_steps": [{}]}]
    fresh = [{"word": "shade", "cefr": "C1", "quiz_steps": [{}]}]
    assert _reading_vocab_candidates_stale(stale, "B2") is True
    assert _reading_vocab_candidates_stale(fresh, "B2") is False


def test_cefr_offset_clamps():
    assert _cefr_offset("B1", 1) == "B2"
    assert _cefr_offset("B1", -1) == "A2"
    assert _cefr_offset("A1", -1) == "A1"
    assert _cefr_offset("C2", 1) == "C2"


def test_ielts_band_known_levels():
    assert _ielts_band("B1") == 5.5
    assert _ielts_band("C1") == 7.5
    assert _ielts_band("???") == 5.5


def test_confidence_pct_normalizes_fraction_and_percent():
    assert _confidence_pct(0.76) == 76.0
    assert _confidence_pct(76) == 76.0
    assert _confidence_pct(None) == 70.0
    assert _confidence_pct(150) == 100.0


def test_total_days_and_levels():
    assert TOTAL_DAYS == 30
    assert READING_DAY_COUNT == 12
    assert len(get_reading_seed(12)["paragraphs"]) >= 5
    assert CEFR_LEVELS[:2] == ["A1", "A2"]


def test_recall_prompts_and_previews():
    svc = VocabCoachingService(session=None)  # pure helpers don't touch the session
    prompts = svc._recall_prompts(
        [{"word": "achieve", "definition": "to succeed", "role": "current"}]
    )
    assert prompts[0]["word"] == "achieve"
    assert "achieve" in prompts[0]["prompt"]

    locked = svc._locked_preview(5, "B1")
    assert "Day 5" in locked and "Day 4" in locked

    nxt = svc._next_day_preview(3, "B2", ["aquifer", "pristine"], "context reading")
    assert "Day 3" in nxt
    assert "aquifer" in nxt


# ----------------------------------------------------------- json normalizers
def test_normalize_reading_questions_filters_and_fixes():
    payload = {
        "questions": [
            {
                "type": "vocabulary",
                "prompt": "What does X mean?",
                "options": ["a", "b", "c"],
                "correct_option": "not-in-options",
                "explanation": "because",
                "source_word": "x",
            },
            {"prompt": "too few options", "options": ["only"]},  # dropped
            {
                "type": "weird-type",
                "prompt": "Comprehension?",
                "options": ["yes", "no"],
                "correct_option": "yes",
            },
        ]
    }
    out = normalize_reading_questions_payload(payload, count=5)
    questions = out["questions"]
    assert len(questions) == 2
    assert questions[0]["correct_option"] in questions[0]["options"]
    assert questions[1]["type"] == "comprehension"  # invalid type coerced


def test_normalize_reading_questions_falls_back():
    fallback = [
        {
            "id": "f1",
            "type": "comprehension",
            "prompt": "p",
            "options": ["a", "b"],
            "correct_option": "a",
            "explanation": "",
        }
    ]
    out = normalize_reading_questions_payload(
        {"questions": []}, count=4, fallback_questions=fallback
    )
    assert out["questions"] == fallback


def test_normalize_coaching_notes_fallbacks():
    out = normalize_coaching_notes_payload(
        {}, context={"level": "B2", "day_number": 2, "looked_up_words": ["aquifer"]}
    )
    assert out["headline"]
    assert len(out["notes"]) >= 2
    assert "aquifer" in out["recommended_words"]


def test_normalize_reading_explain_fallback():
    out = normalize_reading_explain_payload(
        {},
        phrase="former glory",
        sentence="returned them to their former glory.",
        level="B1",
    )
    assert "former glory" in out["explanation"]


def test_build_reading_questions_prompt_includes_signals():
    prompt = build_reading_questions_prompt(
        context={
            "level": "B2",
            "title": "The stepwells of India",
            "passage": passage_text(),
            "looked_up_words": ["aquifer"],
            "count": 3,
        }
    )
    assert "aquifer" in prompt
    assert "stepwells" in prompt.lower()


# ------------------------------------------------------------- mock provider
def test_mock_generate_reading_questions():
    provider = MockLLMProvider()
    out = asyncio.run(
        provider.generate_reading_questions(
            context={
                "level": "B2",
                "count": 4,
                "looked_up_words": ["aquifer", "pristine"],
                "difficult_words": [{"word": "utilitarian"}],
            }
        )
    )
    questions = out["questions"]
    assert len(questions) == 4
    for question in questions:
        assert question["correct_option"] in question["options"]
        assert question["prompt"]


def test_mock_generate_coaching_notes():
    provider = MockLLMProvider()
    out = asyncio.run(
        provider.generate_coaching_notes(
            context={
                "level": "B1",
                "day_number": 1,
                "looked_up_words": ["aquifer"],
                "reading_correct": 2,
                "reading_total": 5,
            }
        )
    )
    assert out["headline"]
    assert out["notes"]
    assert out["next_focus"]


def test_mock_explain_reading_phrase():
    provider = MockLLMProvider()
    out = asyncio.run(
        provider.explain_reading_phrase(
            context={
                "level": "B2",
                "phrase": "former glory",
                "sentence": "recent restoration has returned them to their former glory.",
            }
        )
    )
    assert out["explanation"]
    assert "former glory" in out["explanation"]


# ---------------------------------------------------------- reading coach cache
def test_reading_coach_cache_key_stable():
    kwargs = dict(
        reading_id="cambridge10-stepwells",
        selection_type="word",
        selected_text="groundwater",
        sentence_text="Groundwater is a fundamental resource.",
        locale="vi",
        user_level="B1",
        model_name="gpt-5.5-2026-04-23",
    )
    assert (
        build_reading_coach_cache_key(**kwargs)[0]
        == build_reading_coach_cache_key(**kwargs)[0]
    )


def test_reading_coach_cache_key_differs_by_sentence():
    base = dict(
        reading_id="cambridge10-stepwells",
        selection_type="word",
        selected_text="neglected",
        sentence_text="They were neglected for centuries.",
        locale="vi",
        user_level="B2",
        model_name="gpt-5.5-2026-04-23",
    )
    key_a, _ = build_reading_coach_cache_key(**base)
    key_b, _ = build_reading_coach_cache_key(
        **{**base, "sentence_text": "Recent restoration has returned them."}
    )
    assert key_a != key_b


def test_align_reading_coach_card_rejects_sentence_card_for_word_selection():
    from aiforen.integrations.llm.json_utils import (
        align_reading_coach_card_to_selection,
        normalize_reading_helper_note_payload,
    )

    sentence_card = normalize_reading_helper_note_payload(
        {
            "noteType": "sentence_breakdown",
            "priority": 5,
            "shouldShow": True,
            "title": "Phrase breakdown",
            "targetText": "Richard Cox travelled to north-western India.",
            "meaningVi": "full sentence meaning",
            "mainNoteVi": "sentence map note",
        }
    )
    assert sentence_card["card_type"] == "phrase_breakdown"

    aligned = align_reading_coach_card_to_selection(
        sentence_card,
        reading_selection={
            "selection_type": "word",
            "selected_text": "monuments",
            "sentence_text": "Richard Cox travelled to document these spectacular monuments.",
        },
        locale="en",
    )
    assert aligned["card_type"] == "vocab_context"
    assert aligned["note_type"] == "word_coach"
    target = aligned.get("target") or {}
    assert (
        str(target.get("word") or target.get("text") or "")
        .lower()
        .startswith("monument")
    )


def test_reading_coach_cache_key_differs_by_level():
    base = dict(
        reading_id="cambridge10-stepwells",
        selection_type="word",
        selected_text="groundwater",
        sentence_text="Groundwater is a fundamental resource.",
        locale="vi",
        user_level="B1",
        model_name="gpt-5.5-2026-04-23",
    )
    key_b1, _ = build_reading_coach_cache_key(**base)
    key_b2, _ = build_reading_coach_cache_key(**{**base, "user_level": "B2"})
    assert key_b1 != key_b2


def test_reading_coach_cache_selection_bundle():
    bundle = cache_key_from_selection(
        reading={"id": "cambridge10-stepwells"},
        reading_selection={
            "selection_type": "word",
            "selected_text": "restoration",
            "sentence_text": "recent restoration has returned them.",
            "user_level": "B2",
        },
        locale="vi",
        user_level="B1",
        model_name="gpt-5.5-2026-04-23",
    )
    assert bundle is not None
    _, parts = bundle
    assert parts["user_level"] == "B2"
    assert parts["prompt_version"] == READING_COACH_PROMPT_VERSION


def test_reading_coach_cache_is_cacheable_guard():
    assert normalize_sentence_text("  a   b  ") == "a b"
    assert not is_cacheable_reading_coach_card(
        {
            "should_show": True,
            "main_note": "Giải thích «groundwater» trong câu đang đọc, ưu tiên cụm có nghĩa thật quanh từ.",
        }
    )
    assert is_cacheable_reading_coach_card(
        {
            "should_show": True,
            "main_note": "Trong câu này, groundwater là nước ngầm.",
        }
    )
