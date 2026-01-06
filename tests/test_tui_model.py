from __future__ import annotations

from pathlib import Path

from codex_transcripts.rollout import parse_rollout_file
from codex_transcripts.tui import build_message_units, filter_units, group_units_by_prompt


def test_build_message_units_kinds():
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    session_data, _meta, _stats = parse_rollout_file(rollout)
    units = build_message_units(session_data)

    kinds = {u.kind for u in units}
    assert "user" in kinds
    assert "assistant" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "system" in kinds


def test_filter_units_by_kind_and_text():
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    session_data, _meta, _stats = parse_rollout_file(rollout)
    units = build_message_units(session_data)

    only_user = filter_units(
        units,
        query="hello",
        show_user=True,
        show_assistant=False,
        show_tool_calls=False,
        show_tool_results=False,
        show_thinking=False,
        show_system=False,
    )
    assert only_user
    assert all(u.kind == "user" for u in only_user)


def test_group_units_by_prompt():
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    session_data, _meta, _stats = parse_rollout_file(rollout)
    units = build_message_units(session_data)

    groups = group_units_by_prompt(units)
    assert groups
    first_prompt = next((g.prompt for g in groups if g.prompt is not None), None)
    assert first_prompt is not None
    assert first_prompt.kind == "user"
