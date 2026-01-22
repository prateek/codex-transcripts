from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from click.testing import CliRunner

from codex_transcripts.cli import cli


def _write_min_rollout(path: Path) -> None:
    session_id = "33333333-3333-3333-3333-333333333333"
    ts = "2026-01-05T12:00:00.000Z"
    lines = [
        {
            "timestamp": ts,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": ts,
                "cwd": "/tmp/MAGIC_CWD",
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
    path.write_text(
        "\n".join(json.dumps(obj, ensure_ascii=False) for obj in lines) + "\n",
        encoding="utf-8",
    )


@contextmanager
def _serve_dir(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):  # pragma: no cover
            return

    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_import_then_export_full_cycle(tmp_path: Path):
    # Serve a real .jsonl over HTTP, import into CODEX_HOME, then export via `local --latest`.
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    server_dir = tmp_path / "server"
    server_dir.mkdir()
    filename = "rollout-2026-01-05T12-00-00-33333333-3333-3333-3333-333333333333.jsonl"
    rollout_path = server_dir / filename
    _write_min_rollout(rollout_path)

    runner = CliRunner()
    with _serve_dir(server_dir) as base:
        url = f"{base}/{filename}"

        r1 = runner.invoke(cli, ["import", url, "--codex-home", str(codex_home)])
        assert r1.exit_code == 0, r1.output
        assert "Imported:" in r1.output

    out_html = tmp_path / "out_html"
    r2 = runner.invoke(
        cli,
        ["local", "--latest", "--codex-home", str(codex_home), "-o", str(out_html)],
    )
    assert r2.exit_code == 0, r2.output
    assert (out_html / "index.html").exists()
    assert "Hello Codex" in (out_html / "index.html").read_text(encoding="utf-8")

    out_json = tmp_path / "out_json"
    r3 = runner.invoke(
        cli,
        [
            "local",
            "--latest",
            "--format",
            "json",
            "--codex-home",
            str(codex_home),
            "-o",
            str(out_json),
        ],
    )
    assert r3.exit_code == 0, r3.output
    payload = json.loads((out_json / "transcript.json").read_text(encoding="utf-8"))
    assert payload["format"] == "codex-transcripts.session.v1"
    assert any(
        ll.get("type") == "user" and ll.get("message", {}).get("content") == "Hello Codex"
        for ll in payload.get("session", {}).get("loglines", [])
    )

