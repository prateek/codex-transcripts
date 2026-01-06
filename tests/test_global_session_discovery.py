from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from codex_transcripts.cli import cli
from codex_transcripts.rollout import list_session_rows


def _write_min_rollout(
    path: Path,
    *,
    cwd: str,
    user_message: str = "Hello Codex",
    git_branch: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "id": "00000000-0000-0000-0000-000000000010",
        "timestamp": "2026-01-05T12:00:00.000Z",
        "cwd": cwd,
        "originator": "cli",
        "cli_version": "0.9.0",
        "instructions": None,
        "source": "cli",
        "model_provider": "openai",
    }
    if git_branch is not None:
        payload["git"] = {
            "commit_hash": "deadbeef",
            "branch": git_branch,
            "repository_url": "https://github.com/example/repo.git",
        }

    lines = [
        {"timestamp": "2026-01-05T12:00:00.000Z", "type": "session_meta", "payload": payload},
        {
            "timestamp": "2026-01-05T12:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_message},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(obj, ensure_ascii=False) for obj in lines) + "\n",
        encoding="utf-8",
    )


def test_list_session_rows_query_matches_non_preview_fields(tmp_path: Path):
    codex_home = tmp_path / "codex_home"
    sessions_dir = codex_home / "sessions" / "2026" / "01" / "05"
    sessions_dir.mkdir(parents=True)

    rollout_path = sessions_dir / "rollout-2026-01-05T12-00-00-11111111-1111-1111-1111-111111111111.jsonl"
    _write_min_rollout(rollout_path, cwd="/tmp/MAGIC_CWD", git_branch="feature/magic")

    rows = list_session_rows(
        codex_home=codex_home,
        include_archived=False,
        filter_cwd=None,
        limit=10,
        query="magic_cwd",
    )
    assert len(rows) == 1

    rows = list_session_rows(
        codex_home=codex_home,
        include_archived=False,
        filter_cwd=None,
        limit=10,
        query="feature/",
    )
    assert len(rows) == 1


def test_local_cmd_searches_globally_by_default_and_cwd_filters(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex_home"
    sessions_dir = codex_home / "sessions" / "2026" / "01" / "05"
    sessions_dir.mkdir(parents=True)

    rollout_path = sessions_dir / "rollout-2026-01-05T12-00-00-22222222-2222-2222-2222-222222222222.jsonl"
    _write_min_rollout(rollout_path, cwd="/tmp/MAGIC_CWD")

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "local",
            "--codex-home",
            str(codex_home),
            "--latest",
            "--format",
            "json",
            "-o",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "transcript.json").exists()

    out_dir2 = tmp_path / "out2"
    out_dir2.mkdir()
    result2 = runner.invoke(
        cli,
        [
            "local",
            "--codex-home",
            str(codex_home),
            "--latest",
            "--cwd",
            "--format",
            "json",
            "-o",
            str(out_dir2),
        ],
    )
    assert result2.exit_code != 0
    assert "No Codex sessions found" in result2.output
