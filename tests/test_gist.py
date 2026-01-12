from __future__ import annotations

from pathlib import Path

import pytest

from codex_transcripts.gist import create_gist


def test_create_gist_success(monkeypatch, tmp_path: Path):
    import subprocess

    html_path = tmp_path / "index.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    mock_create = subprocess.CompletedProcess(
        args=["gh", "gist", "create", str(html_path)],
        returncode=0,
        stdout="https://gist.github.com/testuser/abc123def456\n",
        stderr="",
    )
    mock_api = subprocess.CompletedProcess(
        args=["gh", "api", "/gists/abc123def456"],
        returncode=0,
        stdout=(
            "{"
            '"owner":{"login":"testuser"},'
            '"history":[{"version":"deadbeef"}],'
            '"files":{"index.html":{"raw_url":"https://gist.githubusercontent.com/testuser/abc123def456/raw/deadbeef/index.html"}}'
            "}"
        ),
        stderr="",
    )

    captured = {}

    def mock_run(*args, **kwargs):
        cmd = args[0]
        captured.setdefault("cmds", []).append(cmd)
        if cmd[:3] == ["gh", "gist", "create"]:
            return mock_create
        if cmd[:3] == ["gh", "api", "/gists/abc123def456"]:
            return mock_api
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", mock_run)

    gist = create_gist(html_path)
    assert gist.gist_id == "abc123def456"
    assert gist.gist_url.endswith("/abc123def456")
    assert gist.raw_url == "https://gist.githubusercontent.com/testuser/abc123def456/raw/deadbeef/index.html"
    assert (
        gist.preview_url
        == "https://gistcdn.githack.com/testuser/abc123def456/raw/deadbeef/index.html"
    )

    cmds = captured.get("cmds") or []
    assert cmds[0] == ["gh", "gist", "create", str(html_path)]
    assert cmds[1] == ["gh", "api", "/gists/abc123def456"]


def test_create_gist_no_files(tmp_path: Path):
    import click

    with pytest.raises(click.ClickException):
        create_gist(tmp_path / "missing.html")
