from __future__ import annotations

from pathlib import Path

from codex_transcripts.rollout import RolloutParseError, parse_rollout_file


def test_parse_rollout_file_emits_loglines(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    session_data, meta, stats = parse_rollout_file(rollout)

    assert meta is not None
    assert meta.id == "00000000-0000-0000-0000-000000000001"
    assert meta.git is not None
    assert meta.git["repository_url"] == "https://github.com/openai/codex.git"

    loglines = session_data["loglines"]
    assert len(loglines) >= 3
    assert any(e["type"] == "user" for e in loglines)
    assert any(e["type"] == "assistant" for e in loglines)
    assert any(e["type"] == "system" for e in loglines)

    # Includes tool_use and tool_result blocks
    tool_use = [
        e
        for e in loglines
        if e["type"] == "assistant"
        and isinstance(e.get("message", {}).get("content"), list)
        and any(b.get("type") == "tool_use" for b in e["message"]["content"])
    ]
    assert tool_use

    tool_result = [
        e
        for e in loglines
        if e["type"] == "user"
        and isinstance(e.get("message", {}).get("content"), list)
        and any(b.get("type") == "tool_result" for b in e["message"]["content"])
    ]
    assert tool_result

    assert stats.total_lines >= 5
    assert stats.emitted_loglines == len(loglines)


def test_parse_rollout_file_tracks_system_rollout_types():
    rollout = Path(__file__).parent / "sample_rollout_unknown.jsonl"
    session_data, _meta, stats = parse_rollout_file(rollout)

    assert stats.system_rollout_types.get("totally_new_type") == 1
    assert any(e["type"] == "system" for e in session_data["loglines"])


def test_parse_rollout_file_tracks_system_event_types():
    rollout = Path(__file__).parent / "sample_rollout_unknown_event.jsonl"
    session_data, _meta, stats = parse_rollout_file(rollout)

    assert stats.system_event_types.get("mystery_event") == 1
    assert any(e["type"] == "system" for e in session_data["loglines"])


def test_parse_rollout_file_tracks_system_response_item_types():
    rollout = Path(__file__).parent / "sample_rollout_unknown_response_item.jsonl"
    session_data, _meta, stats = parse_rollout_file(rollout)

    assert stats.system_response_item_types.get("mystery_item") == 1
    assert any(e["type"] == "system" for e in session_data["loglines"])


def test_parse_rollout_file_recognizes_more_event_types():
    rollout = Path(__file__).parent / "sample_rollout_known_event_types.jsonl"
    session_data, _meta, stats = parse_rollout_file(rollout)

    assert session_data["loglines"]
    assert not stats.system_event_types
