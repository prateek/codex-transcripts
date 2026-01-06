from __future__ import annotations

from pathlib import Path

from codex_transcripts.rollout import (
    SessionRow,
    calculate_resume_style_metrics,
    format_resume_style_header,
    format_resume_style_row,
)


def _row(*, preview: str, branch: str | None = None, cwd: str | None = None) -> SessionRow:
    return SessionRow(
        path=Path("rollout-2026-01-01T00-00-00-00000000-0000-0000-0000-000000000000.jsonl"),
        session_id=None,
        preview=preview,
        created_at=None,
        updated_at=None,
        cwd=cwd,
        git_branch=branch,
        source=None,
        model_provider=None,
    )


def test_resume_style_header_and_row_no_cwd():
    rows = [_row(preview="hello")]
    metrics = calculate_resume_style_metrics(rows, show_cwd=False)

    assert metrics.max_updated_width == len("Updated")
    assert metrics.max_branch_width == len("Branch")
    assert metrics.max_cwd_width == 0

    assert format_resume_style_header(metrics) == "Updated  Branch  Conversation"
    assert (
        format_resume_style_row(rows[0], metrics=metrics)
        == "-".ljust(len("Updated")) + "  " + "-".ljust(len("Branch")) + "  " + "hello"
    )


def test_resume_style_header_and_row_with_cwd():
    rows = [
        _row(preview="hello", cwd="/tmp/project", branch="main"),
        _row(preview="world", cwd="/tmp/project", branch=None),
    ]
    metrics = calculate_resume_style_metrics(rows, show_cwd=True)

    assert metrics.max_updated_width == len("Updated")
    assert metrics.max_branch_width == len("Branch")
    assert metrics.max_cwd_width >= len("CWD")

    header = format_resume_style_header(metrics)
    assert "Updated" in header
    assert "Branch" in header
    assert "CWD" in header
    assert header.endswith("Conversation")

    formatted = format_resume_style_row(rows[0], metrics=metrics)
    assert "main" in formatted
    assert "/tmp/project" in formatted


def test_preview_is_truncated():
    long_preview = "x" * 500
    row = _row(preview=long_preview)
    metrics = calculate_resume_style_metrics([row], show_cwd=False)
    formatted = format_resume_style_row(row, metrics=metrics)
    assert len(formatted) < len(long_preview)
