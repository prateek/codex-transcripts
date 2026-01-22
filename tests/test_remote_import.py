from __future__ import annotations

import json
from pathlib import Path

import click
import httpx
import pytest
from click.testing import CliRunner

from codex_transcripts.cli import cli
from codex_transcripts.remote import ImportedSession, import_rollout_url
from codex_transcripts.rollout import list_session_rows


def _jsonl(lines: list[dict[str, object]]) -> bytes:
    return ("\n".join(json.dumps(obj, ensure_ascii=False) for obj in lines) + "\n").encode("utf-8")


def _min_rollout_bytes(*, session_id: str, timestamp: str, cwd: str = "/tmp/MAGIC_CWD") -> bytes:
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": timestamp,
                "cwd": cwd,
                "originator": "cli",
                "cli_version": "0.9.0",
                "instructions": None,
                "source": "cli",
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2026-01-05T12:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Hello Codex"},
        },
    ]
    return _jsonl(lines)


def test_import_rollout_url_preserves_rollout_filename_and_discovers_session(tmp_path: Path):
    session_id = "11111111-1111-1111-1111-111111111111"
    timestamp = "2026-01-05T12:00:00.000Z"
    url = (
        "https://example.com/rollout-2026-01-05T12-00-00-"
        "11111111-1111-1111-1111-111111111111.jsonl"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, content=_min_rollout_bytes(session_id=session_id, timestamp=timestamp))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    imported = import_rollout_url(url, codex_home=tmp_path, http_client=client)
    assert imported.path.exists()
    assert imported.path.parent == tmp_path / "sessions" / "2026" / "01" / "05"
    assert imported.path.name.endswith(f"-{session_id}.jsonl")

    rows = list_session_rows(codex_home=tmp_path, include_archived=False, limit=10)
    assert len(rows) == 1
    assert rows[0].path == imported.path
    assert rows[0].preview == "Hello Codex"


def test_import_rollout_url_generates_rollout_filename_when_url_is_not_rollout(tmp_path: Path):
    session_id = "00000000-0000-0000-0000-000000000010"
    timestamp = "2026-01-05T12:00:00.000Z"
    url = "https://example.com/sessions/my-session"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, content=_min_rollout_bytes(session_id=session_id, timestamp=timestamp))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    imported = import_rollout_url(url, codex_home=tmp_path, http_client=client)
    assert imported.path.exists()
    assert imported.path.parent == tmp_path / "sessions" / "2026" / "01" / "05"
    assert imported.path.name == f"rollout-2026-01-05T12-00-00-{session_id}.jsonl"


def test_import_rollout_url_rejects_non_rollout_jsonl(tmp_path: Path):
    url = "https://example.com/not-a-rollout.jsonl"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_jsonl(
                [
                    {
                        "timestamp": "2026-01-05T12:00:01.000Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "Hello"},
                    }
                ]
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(click.ClickException, match="missing session_meta"):
        import_rollout_url(url, codex_home=tmp_path, http_client=client)


def test_import_cmd_wires_to_import_rollout_url(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex_home"
    expected = codex_home / "sessions" / "2026" / "01" / "05" / "rollout-2026-01-05T12-00-00-abc.jsonl"
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_text("{}", encoding="utf-8")

    captured: dict[str, object] = {}

    def stub_import(url: str, *, codex_home: Path | None, archived: bool, overwrite: bool, max_bytes: int):
        captured.update(
            {
                "url": url,
                "codex_home": codex_home,
                "archived": archived,
                "overwrite": overwrite,
                "max_bytes": max_bytes,
            }
        )
        return ImportedSession(url=url, path=expected, session_id="abc", timestamp="2026-01-05T12:00:00.000Z")

    import codex_transcripts.cli as cli_mod

    monkeypatch.setattr(cli_mod, "import_rollout_url", stub_import)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "import",
            "https://example.com/rollout.jsonl",
            "--codex-home",
            str(codex_home),
            "--max-bytes",
            "123",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"Imported: {expected}" in result.output
    assert captured["url"] == "https://example.com/rollout.jsonl"
    assert captured["codex_home"] == codex_home
    assert captured["archived"] is False
    assert captured["overwrite"] is False
    assert captured["max_bytes"] == 123

