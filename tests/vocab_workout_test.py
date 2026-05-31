from aiforen.domain.vocab_workout import (
    MAX_MICRO_REPAIRS,
    TASK_SKILLS,
    canonical_skill,
    choose_focus_skill,
    compose_workout_items,
    intensity_for,
    mastery_slot_credit_key,
    select_micro_repair,
)


def _candidate(index: int, *, slot: int, task_type: str, interaction: str = "mcq"):
    return {
        "question_id": f"q-{index}",
        "word_id": f"w-{index}",
        "mastery_slot": slot,
        "task_type": task_type,
        "skill": "meaning",
        "interaction_kind": interaction,
    }


def test_cefr_catalog_maps_all_documented_task_types():
    expected = {
        "vn_meaning_mcq",
        "meaning_mcq",
        "simple_cloze",
        "sentence_reorder",
        "translate_with_hints",
        "meaning_in_context",
        "collocation_mcq",
        "pattern_cloze",
        "error_diagnosis",
        "guided_rewrite",
        "error_correction",
        "targeted_rewrite_challenge",
        "nuance_in_context",
        "academic_collocation",
        "register_choice",
        "rewrite_with_target",
        "precision_nuance_challenge",
        "precision_in_context",
        "register_tone_judgment",
        "precision_cloze",
        "advanced_paraphrase",
        "nuance_rationale_challenge",
    }
    assert expected <= TASK_SKILLS.keys()
    assert all(canonical_skill(task) for task in expected)


def test_intensity_is_adaptive():
    assert (
        intensity_for(learner_rhythm="intermittent", due_today=0, daily_goal=5)
        == "recovery"
    )
    assert (
        intensity_for(learner_rhythm="early", due_today=3, daily_goal=5) == "standard"
    )
    assert (
        intensity_for(learner_rhythm="consistent", due_today=1, daily_goal=5) == "depth"
    )
    assert (
        intensity_for(learner_rhythm="consistent", due_today=14, daily_goal=5)
        == "recovery"
    )


def test_focus_skill_uses_strongest_issue_then_due_state():
    assert (
        choose_focus_skill(
            weaknesses=[
                {"dimension": "meaning", "severity": 1},
                {"dimension": "collocation_mcq_wrong", "severity": 3},
            ]
        )
        == "collocation"
    )
    assert (
        choose_focus_skill(
            weaknesses=[],
            skill_states=[{"skill_id": "pattern", "due_at": "2026-01-01", "score": -2}],
            due_today=2,
        )
        == "pattern"
    )


def test_focus_skill_tie_break_prefers_latest_issue_then_earliest_due_word():
    assert (
        choose_focus_skill(
            weaknesses=[
                {
                    "dimension": "meaning",
                    "severity": 3,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                    "due_at": "2026-05-02T00:00:00+00:00",
                    "word_id": "word-a",
                },
                {
                    "dimension": "pattern",
                    "severity": 3,
                    "last_seen_at": "2026-05-03T00:00:00+00:00",
                    "due_at": "2026-05-04T00:00:00+00:00",
                    "word_id": "word-b",
                },
            ]
        )
        == "pattern"
    )


def test_mastery_credit_key_is_slot_based_for_matrix_questions():
    assert (
        mastery_slot_credit_key(
            track_id="cefr:B2",
            mastery_slot=3,
            fallback_question_id="question-a",
            task_type="collocation_mcq",
        )
        == "cefr:B2:slot:3"
    )


def test_compose_workout_has_three_phases_and_item_cap():
    candidates = [
        _candidate(1, slot=1, task_type="meaning_mcq"),
        _candidate(2, slot=2, task_type="meaning_in_context"),
        _candidate(3, slot=2, task_type="collocation_mcq"),
        _candidate(4, slot=2, task_type="collocation_mcq"),
        _candidate(5, slot=3, task_type="collocation_mcq"),
        _candidate(6, slot=4, task_type="error_correction", interaction="rewrite"),
        _candidate(
            7, slot=5, task_type="targeted_rewrite_challenge", interaction="rewrite"
        ),
    ]
    items = compose_workout_items(
        candidates=candidates, focus_skill="collocation", intensity="standard"
    )
    assert {"warmup", "focus", "stretch"} <= {item["phase"] for item in items}
    assert len(items) <= 12
    assert all(item["is_required"] for item in items)


def test_micro_repair_is_same_skill_and_capped():
    candidates = [
        _candidate(1, slot=2, task_type="collocation_mcq"),
        _candidate(2, slot=2, task_type="meaning_mcq"),
        _candidate(3, slot=3, task_type="collocation_mcq"),
    ]
    repair = select_micro_repair(
        candidates=candidates,
        failed_item={"question_id": "q-1", "skill_id": "collocation"},
        existing_repairs=0,
    )
    assert repair
    assert repair["question_id"] == "q-3"
    assert (
        select_micro_repair(
            candidates=candidates,
            failed_item={"question_id": "q-1", "skill_id": "collocation"},
            existing_repairs=MAX_MICRO_REPAIRS,
        )
        is None
    )
