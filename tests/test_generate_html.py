from __future__ import annotations

from pathlib import Path

from codex_transcripts.transcript import generate_html_from_rollout


def test_generate_html_creates_index_and_chunks(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    out_dir, meta, stats = generate_html_from_rollout(rollout, tmp_path / "out")

    assert (out_dir / "index.html").exists()
    assert (out_dir / "chunks" / "chunk-000.js").exists()

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "Codex transcript" in index_html
    assert "Search" in index_html
    assert 'id="theme-toggle"' in index_html
    assert 'id="minimap"' in index_html
    assert 'id="kb-help"' in index_html
    assert 'class="conversation index-item"' in index_html
    assert "prefers-color-scheme" in index_html

    chunk_js = (out_dir / "chunks" / "chunk-000.js").read_text(encoding="utf-8")
    assert "Hello Codex" in chunk_js
    assert "echo hi" in chunk_js

    assert meta is not None
    assert stats.emitted_loglines > 0


def test_generate_html_includes_format_drift_warning(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout_unknown_event.jsonl"
    out_dir, _meta, stats = generate_html_from_rollout(rollout, tmp_path / "out")

    assert stats.system_event_types
    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "System/internal records" in index_html
    assert "event_msg" in index_html
    assert "mystery_event" in index_html

    chunk_js = (out_dir / "chunks" / "chunk-000.js").read_text(encoding="utf-8")
    assert "system-record" in chunk_js
    assert "event_msg:mystery_event" in chunk_js


def test_generate_html_omits_format_drift_warning_when_clean(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout.jsonl"
    out_dir, _meta, stats = generate_html_from_rollout(rollout, tmp_path / "out")

    assert not stats.system_rollout_types
    assert not stats.system_event_types
    assert not stats.system_response_item_types
    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "System/internal records" not in index_html


def test_generate_html_includes_format_drift_warning_for_unknown_rollout_type(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout_unknown.jsonl"
    out_dir, _meta, stats = generate_html_from_rollout(rollout, tmp_path / "out")

    assert stats.system_rollout_types.get("totally_new_type") == 1
    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "System/internal records" in index_html
    assert "totally_new_type" in index_html

    assert (out_dir / "chunks" / "chunk-000.js").exists()
    chunk_js = (out_dir / "chunks" / "chunk-000.js").read_text(encoding="utf-8")
    assert "system-record" in chunk_js
    assert "rollout:totally_new_type" in chunk_js


def test_generate_html_includes_format_drift_warning_for_unknown_response_item_type(tmp_path: Path):
    rollout = Path(__file__).parent / "sample_rollout_unknown_response_item.jsonl"
    out_dir, _meta, stats = generate_html_from_rollout(rollout, tmp_path / "out")

    assert stats.system_response_item_types.get("mystery_item") == 1
    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "System/internal records" in index_html
    assert "mystery_item" in index_html

    chunk_js = (out_dir / "chunks" / "chunk-000.js").read_text(encoding="utf-8")
    assert "system-record" in chunk_js
    assert "response_item:mystery_item" in chunk_js
