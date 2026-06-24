"""Tests for word-task mission assembly."""

from aiforen.domain.vocab_task_templates import get_vocab_task_template
from aiforen.domain.vocab_word_mission import (
    expand_word_tasks_to_plan_blocks,
    normalize_vocab_daily_word_mission_payload,
)


def test_normalize_clamps_word_count():
    ctx = {
        "stats": {"due_today": 2},
        "user_profile": {"daily_goal": 6},
        "learner_stage": "growth_3_to_30_days",
        "learner_rhythm": "consistent",
        "mission_signals": {"primary_mission_type": "study_pack"},
    }
    out = normalize_vocab_daily_word_mission_payload(
        {
            "session_template_id": "invalid_id",
            "target_word_count": 99,
            "word_tasks": [],
            "coach_overview_lines": ["line"],
        },
        context=ctx,
    )
    assert out["session_template_id"] == "study_learn_then_quiz"
    assert out["target_word_count"] <= 12


def test_expand_one_block_per_word():
    template = get_vocab_task_template("repair_meaning_mcq")
    assert template is not None
    tasks = [
        {
            "word_id": "w1",
            "lemma_hint": "abandon",
            "source": "wrong_answer",
            "priority": 1,
        },
        {"word_id": "w2", "lemma_hint": "bias", "source": "due", "priority": 2},
    ]
    blocks = expand_word_tasks_to_plan_blocks(
        word_tasks=tasks,
        template=template,
        pack_id="pack-1",
        locale="vi",
    )
    assert len(blocks) == 2
    assert blocks[0]["word_ids"] == ["w1"]
    assert blocks[0]["target_count"] == 1
    assert blocks[0]["task_id"] == "wt-w1"
