"""Tests for vocab_storage coaching reading schema v2 loader."""

from pathlib import Path

from aiforen.domain.coaching_reading_v2 import (
    editorial_question_type_to_db,
    load_all_v2_upsert_payloads,
    load_v2_unit,
    question_to_db_row,
    unit_to_upsert_payload,
)

_A1_PATH = (
    Path(__file__).resolve().parents[2]
    / "vocab_storage"
    / "coaching_reading"
    / "a1-day01-small-bag.json"
)


def test_a1_v2_unit_loads():
    unit = load_v2_unit(_A1_PATH)
    assert unit["lesson_type"] == "reading_vocab"
    assert unit["status"] == "published"
    assert len(unit["paragraphs"]) == 4
    assert len(unit["target_vocabulary"]) == 8
    assert len(unit["questions"]) == 8


def test_v2_question_maps_to_db_rows():
    unit = load_v2_unit(_A1_PATH)
    payload = unit_to_upsert_payload(unit)
    assert payload["unit_id"] == "a1-day01-small-bag"
    assert payload["question_limit"] == 8
    assert payload["status"] == "published"
    assert len(payload["paragraphs"]) == 4
    assert payload["vocab_keywords"][0]["lemma"] == "bag"

    mcq = next(q for q in unit["questions"] if q["id"] == "a1-d01-q01")
    row = question_to_db_row(mcq)
    assert row["question_type"] == "comprehension"
    assert row["correct_option"] == "A small bag"
    assert "A small bag" in row["options"]

    gap = next(q for q in unit["questions"] if q["id"] == "a1-d01-q05")
    gap_row = question_to_db_row(gap)
    assert gap_row["question_type"] == "gap_fill"
    assert gap_row["correct_option"] == "pen"

    inference = next(q for q in unit["questions"] if q["id"] == "a1-d01-q08")
    assert editorial_question_type_to_db(inference["question_type"]) == "comprehension"
    assert question_to_db_row(inference)["question_type"] == "comprehension"


def test_editorial_type_mapping():
    assert editorial_question_type_to_db("writer_purpose_mcq") == "comprehension"
    assert editorial_question_type_to_db("vocab_in_context_mcq") == "vocabulary"
    assert editorial_question_type_to_db("gap_fill") == "gap_fill"
    assert editorial_question_type_to_db("unknown_type") == "comprehension"


def test_load_all_v2_payloads():
    payloads = load_all_v2_upsert_payloads()
    assert len(payloads) >= 7
    ids = {p["unit_id"] for p in payloads}
    assert "b2-day02-food-waste-supermarkets" in ids
    for payload in payloads:
        assert payload["status"] == "published"
        for q in payload["questions"]:
            assert q["question_type"] in {"comprehension", "vocabulary", "gap_fill"}
