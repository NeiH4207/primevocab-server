"""Unit tests for level-based coaching reading catalog."""

from types import SimpleNamespace

from aiforen.domain.coaching_content import (
    grade_reading_answer,
    normalize_question,
    passage_tokens_from_paragraphs,
    placeholder_reading,
    should_refresh_reading_snapshot,
    unit_to_reading_payload,
)

A2_DAY1_PARAGRAPHS = [
    "Linh lives on a busy street in the city. In the morning, many cars and motorbikes "
    "go past her house. The street is often noisy and crowded.",
    "Near Linh's home, there is a small park. It has trees, flowers, and two long "
    "benches. After school, Linh sometimes goes there with her younger brother. They "
    "sit under a tree and relax.",
    "Many local people use the park. Children play on the grass. Some students read "
    "books on the benches. In the evening, older people walk slowly around the park.",
    "The park is useful, but it needs care. People should not leave rubbish on the "
    "grass. Linh thinks everyone should help keep the park clean.",
]

A2_DAY1_QUESTIONS = [
    {"id": "a2-d01-q01", "sort_order": 1, "question_type": "comprehension"},
    {"id": "a2-d01-q02", "sort_order": 2, "question_type": "comprehension"},
    {"id": "a2-d01-q03", "sort_order": 3, "question_type": "comprehension"},
    {"id": "a2-d01-q04", "sort_order": 4, "question_type": "comprehension"},
    {"id": "a2-d01-q05", "sort_order": 5, "question_type": "gap_fill"},
    {"id": "a2-d01-q06", "sort_order": 6, "question_type": "vocabulary"},
    {"id": "a2-d01-q07", "sort_order": 7, "question_type": "vocabulary"},
    {"id": "a2-d01-q08", "sort_order": 8, "question_type": "comprehension"},
]


def _a2_day1_unit():
    questions = []
    for row in A2_DAY1_QUESTIONS:
        questions.append(
            SimpleNamespace(
                id=row["id"],
                sort_order=row["sort_order"],
                question_type=row["question_type"],
                prompt=row.get("prompt", row["id"]),
                options=row.get("options") or [],
                correct_option=row.get("correct_option", "A"),
                acceptable_answers=row.get("acceptable_answers") or [],
                explanation=row.get("explanation"),
                source_word=row.get("source_word"),
            )
        )
    return SimpleNamespace(
        id="a2-day01-quiet-park",
        cefr_level="A2",
        day_number=1,
        topic_slug="quiet-park",
        topic_title="City life & green spaces",
        title="A Quiet Park",
        source_label="PrimeVocab Original · A2",
        estimated_minutes=8,
        question_limit=8,
        content_version=1,
        paragraphs=A2_DAY1_PARAGRAPHS,
        vocab_keywords=[{"lemma": "park", "pos": "noun", "vi_gloss": "công viên"}],
        questions=questions,
    )


def test_a2_day1_payload_has_four_paragraphs_and_eight_questions():
    payload = unit_to_reading_payload(_a2_day1_unit(), [])
    assert payload["content_unit_id"] == "a2-day01-quiet-park"
    assert payload["target_cefr"] == "A2"
    assert len(payload["paragraphs"]) == 4
    assert len(payload["questions"]) == 8
    assert payload["question_limit"] == 8
    types = {q["question_type"] for q in payload["questions"]}
    assert "gap_fill" in types
    assert "vocabulary" in types


def test_gap_fill_grade_park_is_case_insensitive():
    question = normalize_question(
        {
            "id": "q5",
            "question_type": "gap_fill",
            "prompt": "walk slowly around the ____",
            "options": [],
            "correct_option": "park",
            "acceptable_answers": ["park"],
        }
    )
    assert grade_reading_answer(question, "Park") is True
    assert grade_reading_answer(question, " street ") is False


def test_true_false_grade_false():
    question = normalize_question(
        {
            "id": "q2",
            "question_type": "true_false",
            "prompt": "True or False",
            "options": ["True", "False"],
            "correct_option": "False",
        }
    )
    assert grade_reading_answer(question, "False") is True
    assert grade_reading_answer(question, "True") is False


def test_placeholder_for_missing_unit():
    payload = placeholder_reading("B1", 1)
    assert payload["placeholder"] is True
    assert payload["target_cefr"] == "B1"
    assert payload["paragraphs"] == []
    assert payload["questions"] == []
    assert "coming soon" in payload["title"].lower()


def test_b2_gap_fill_drainage_system():
    question = normalize_question(
        {
            "id": "b2-d01-q04",
            "question_type": "gap_fill",
            "prompt": "put pressure on ____",
            "options": [],
            "correct_option": "drainage system",
            "acceptable_answers": ["drainage system"],
        }
    )
    assert grade_reading_answer(question, "drainage system") is True
    assert grade_reading_answer(question, "Drainage System") is True
    assert grade_reading_answer(question, "flooding") is False


def test_unit_payload_includes_vocab_keyword_seeds():
    unit = SimpleNamespace(
        id="b2-day01-urban-heat",
        cefr_level="B2",
        day_number=1,
        topic_slug="urban-heat",
        topic_title="Urban climate",
        title="City Trees and Urban Heat",
        source_label="PrimeVocab Original · B2",
        estimated_minutes=10,
        question_limit=10,
        content_version=2,
        paragraphs=["One paragraph."],
        vocab_keywords=[{"lemma": "absorb", "pos": "verb", "vi_gloss": "hấp thụ"}],
        questions=[],
    )
    payload = unit_to_reading_payload(unit, [])
    assert payload["vocab_keyword_seeds"] == unit.vocab_keywords


def test_passage_tokens_from_catalog_paragraphs():
    tokens = passage_tokens_from_paragraphs(A2_DAY1_PARAGRAPHS)
    assert "crowded" in tokens
    assert "relax" in tokens
    assert len(tokens) == len(set(tokens))


def _catalog_unit(**overrides):
    base = {
        "id": "b2-day01-city-trees-urban-heat",
        "content_version": 3,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_should_refresh_when_catalog_unit_id_changes():
    reading = {
        "content_unit_id": "b2-day01-urban-heat",
        "content_version": 5,
        "placeholder": False,
    }
    unit = _catalog_unit()
    assert (
        should_refresh_reading_snapshot(
            reading, unit, reading_answers={}, reading_status="pending"
        )
        is True
    )


def test_should_not_refresh_when_snapshot_matches_catalog():
    reading = {
        "content_unit_id": "b2-day01-city-trees-urban-heat",
        "content_version": 3,
        "placeholder": False,
    }
    unit = _catalog_unit()
    assert (
        should_refresh_reading_snapshot(
            reading, unit, reading_answers={}, reading_status="pending"
        )
        is False
    )


def test_should_not_refresh_completed_day_even_if_catalog_changed():
    reading = {
        "content_unit_id": "b2-day01-urban-heat",
        "content_version": 1,
        "placeholder": False,
    }
    unit = _catalog_unit()
    assert (
        should_refresh_reading_snapshot(
            reading, unit, reading_answers={}, reading_status="completed"
        )
        is False
    )
