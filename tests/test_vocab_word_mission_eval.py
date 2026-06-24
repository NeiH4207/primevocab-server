"""Eval-style checks for word-task mission rules (no LLM)."""

import json
from pathlib import Path

from aiforen.domain.vocab_task_templates import get_vocab_task_template
from aiforen.domain.vocab_word_mission import (
    build_rules_word_mission_payload,
    build_template_catalog_for_context,
    expand_word_tasks_to_plan_blocks,
    normalize_vocab_daily_word_mission_payload,
)


def _load_fixture() -> dict:
    path = Path(__file__).parent / "fixtures" / "mission_context_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_catalog_and_normalize():
    ctx = _load_fixture()
    catalog = build_template_catalog_for_context(ctx)
    assert len(catalog) >= 10
    payload = normalize_vocab_daily_word_mission_payload(
        {
            "session_template_id": "repair_meaning_mcq",
            "target_word_count": 4,
            "word_tasks": [{"source": "wrong_answer", "lemma_hint": "test"}],
            "coach_overview_lines": ["a", "b"],
        },
        context=ctx,
    )
    assert payload["session_template_id"] == "repair_meaning_mcq"
    assert 3 <= payload["target_word_count"] <= 12


def test_rules_fallback_template_matches_mission_type():
    ctx = _load_fixture()
    rules = build_rules_word_mission_payload(ctx)
    template = get_vocab_task_template(rules["session_template_id"])
    assert template is not None
    blocks = expand_word_tasks_to_plan_blocks(
        word_tasks=[],
        template=template,
        pack_id="pack-demo",
        locale="vi",
    )
    assert blocks == []
