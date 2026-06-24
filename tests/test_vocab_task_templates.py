"""Tests for vocab task template catalog."""

from aiforen.domain.vocab_task_templates import (
    ALLOWED_TASK_STEPS,
    VOCAB_TASK_TEMPLATES,
    all_vocab_task_templates,
    build_template_catalog_for_context,
    compute_word_count_range,
    get_vocab_task_template,
)


def test_template_count_in_range():
    templates = all_vocab_task_templates()
    assert 20 <= len(templates) <= 35


def test_unique_template_ids():
    ids = [t.id for t in VOCAB_TASK_TEMPLATES]
    assert len(ids) == len(set(ids))


def test_task_steps_allowed():
    for template in VOCAB_TASK_TEMPLATES:
        assert all(step in ALLOWED_TASK_STEPS for step in template.task_steps)


def test_get_template():
    t = get_vocab_task_template("study_learn_then_quiz")
    assert t is not None
    assert t.task_steps == ("learn", "mcq")


def test_catalog_filters_by_band():
    ctx = {"user_profile": {"current_band": 4.5}}
    catalog = build_template_catalog_for_context(ctx)
    ids = {entry["id"] for entry in catalog}
    assert "study_foundation_band" in ids
    assert "study_gre_precision" not in ids


def test_word_count_range_dynamic():
    ctx = {
        "stats": {"due_today": 8},
        "user_profile": {"daily_goal": 6},
        "learner_stage": "growth_3_to_30_days",
        "learner_rhythm": "consistent",
    }
    r = compute_word_count_range(ctx)
    assert r["min"] >= 3
    assert r["max"] >= r["min"]
    assert r["max"] <= 20
