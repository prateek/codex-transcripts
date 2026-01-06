from __future__ import annotations

from pathlib import Path

import pytest

from codex_transcripts.gist import GIST_PREVIEW_JS, create_gist, inject_gist_preview_js


def test_inject_gist_preview_js(tmp_path: Path):
    html_path = tmp_path / "index.html"
    html_path.write_text("<html><body>Hello</body></html>", encoding="utf-8")

    inject_gist_preview_js(tmp_path)
    updated = html_path.read_text(encoding="utf-8")
    assert GIST_PREVIEW_JS in updated


def test_create_gist_success(monkeypatch, tmp_path: Path):
    import subprocess

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "chunk-000.js").write_text("console.log('hi')", encoding="utf-8")

    mock_result = subprocess.CompletedProcess(
        args=["gh", "gist", "create"],
        returncode=0,
        stdout="https://gist.github.com/testuser/abc123def456\n",
        stderr="",
    )

    captured = {}

    def mock_run(*args, **kwargs):
        captured["cmd"] = args[0]
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run)

    gist_id, gist_url = create_gist(tmp_path)
    assert gist_id == "abc123def456"
    assert gist_url.endswith("/abc123def456")

    cmd = captured.get("cmd") or []
    assert any(str(tmp_path / "chunks" / "chunk-000.js") == part for part in cmd)


def test_create_gist_no_files(tmp_path: Path):
    import click

    with pytest.raises(click.ClickException):
        create_gist(tmp_path)
