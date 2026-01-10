from __future__ import annotations

import json
import tempfile
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_transcripts.render import (
    CSS,
    JS,
    analyze_conversation,
    format_tool_stats,
    get_template,
    make_msg_id,
    render_chat_message,
    render_markdown_text,
    render_message,
)
from codex_transcripts.rollout import ParseStats, SessionMeta, parse_rollout_file


TRANSCRIPT_CHUNK_SIZE = 200


def _generate_pagination_html(current_page: int, total_pages: int) -> str:
    return get_template("macros.html").module.pagination(current_page, total_pages)


def _generate_index_pagination_html(total_pages: int) -> str:
    return get_template("macros.html").module.index_pagination(total_pages)


def _is_tool_result_message(message_data: dict[str, Any]) -> bool:
    content = message_data.get("content", [])
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def _is_tool_call_message(message_data: dict[str, Any]) -> bool:
    content = message_data.get("content", [])
    if not isinstance(content, list) or not content:
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)


def _classify_message_kind(log_type: str, message_data: dict[str, Any]) -> str:
    # Used for minimap + keyboard nav in the HTML viewer.
    if log_type == "assistant":
        return "tool_call" if _is_tool_call_message(message_data) else "assistant"
    if log_type == "user":
        return "tool_reply" if _is_tool_result_message(message_data) else "user"
    if log_type == "system":
        return "system"
    return "system"


def _write_transcript_chunks(
    *,
    output_dir: Path,
    items_html: list[str],
    chunk_size: int,
) -> list[str]:
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths: list[str] = []
    for chunk_idx in range((len(items_html) + chunk_size - 1) // chunk_size):
        start = chunk_idx * chunk_size
        chunk_items = items_html[start : start + chunk_size]
        filename = f"chunk-{chunk_idx:03d}.js"

        # JSON is valid JS, and using an external script avoids any </script> HTML parsing issues.
        payload = json.dumps(chunk_items, ensure_ascii=False)
        js = (
            f"(function(){{\n"
            f"  var items = {payload};\n"
            f"  if (window.__CODEX_TRANSCRIPTS__ && typeof window.__CODEX_TRANSCRIPTS__.registerChunk === 'function') {{\n"
            f"    window.__CODEX_TRANSCRIPTS__.registerChunk({chunk_idx}, items);\n"
            f"  }} else {{\n"
            f"    window.__CODEX_TRANSCRIPTS__ = window.__CODEX_TRANSCRIPTS__ || {{}};\n"
            f"    window.__CODEX_TRANSCRIPTS__.chunks = window.__CODEX_TRANSCRIPTS__.chunks || {{}};\n"
            f"    window.__CODEX_TRANSCRIPTS__.chunks[{chunk_idx}] = items;\n"
            f"  }}\n"
            f"}})();\n"
        )
        (chunks_dir / filename).write_text(js, encoding="utf-8")
        chunk_paths.append(f"chunks/{filename}")
    return chunk_paths


def _format_drift_warning_html(stats: ParseStats | None) -> str:
    if stats is None:
        return ""
    system_total = (
        sum(stats.system_rollout_types.values())
        + sum(stats.system_event_types.values())
        + sum(stats.system_response_item_types.values())
    )
    if system_total <= 0:
        return ""

    rollout_json = (
        json.dumps(stats.system_rollout_types, indent=2, ensure_ascii=False)
        if stats.system_rollout_types
        else ""
    )
    event_json = (
        json.dumps(stats.system_event_types, indent=2, ensure_ascii=False)
        if stats.system_event_types
        else ""
    )
    response_json = (
        json.dumps(stats.system_response_item_types, indent=2, ensure_ascii=False)
        if stats.system_response_item_types
        else ""
    )
    return get_template("macros.html").module.system_records_notice(
        system_total, rollout_json, event_json, response_json
    )


def _parse_rfc3339(ts: str) -> datetime | None:
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _format_duration_ms(ms: int | None) -> str:
    if ms is None or ms < 0:
        return "-"
    secs = ms // 1000
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    rem = secs % 60
    if mins < 60:
        return f"{mins}m {rem:02d}s"
    hours = mins // 60
    mins_rem = mins % 60
    return f"{hours}h {mins_rem:02d}m"


def _collect_transcript_items(
    session_data: dict[str, Any],
    *,
    github_repo: str | None,
    collect_html: bool = True,
) -> tuple[list[str], list[str], list[str], list[str], list[tuple[str, str, str]]]:
    loglines = session_data.get("loglines", [])

    transcript_items_html: list[str] = []
    transcript_item_ids: list[str] = []
    transcript_item_kinds: list[str] = []
    transcript_item_timestamps: list[str] = []
    transcript_item_messages: list[tuple[str, str, str]] = []

    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        message_data = entry.get("message", {})
        if not isinstance(log_type, str) or not isinstance(message_data, dict):
            continue
        if not message_data:
            continue

        message_json = json.dumps(message_data, ensure_ascii=False)
        msg_html = render_message(log_type, message_json, timestamp, github_repo)
        if not msg_html:
            continue

        if collect_html:
            transcript_items_html.append(msg_html)
        transcript_item_ids.append(make_msg_id(timestamp))
        transcript_item_kinds.append(_classify_message_kind(log_type, message_data))
        transcript_item_timestamps.append(timestamp)
        transcript_item_messages.append((log_type, message_json, timestamp))

    return (
        transcript_items_html,
        transcript_item_ids,
        transcript_item_kinds,
        transcript_item_timestamps,
        transcript_item_messages,
    )


def _build_conversation_groups(
    transcript_item_messages: list[tuple[str, str, str]],
    transcript_item_timestamps: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    # Conversation groups: split on user prompts, but include any leading system events as the
    # first group ("session start").
    groups: list[dict[str, Any]] = []
    current_prompt: str | None = None
    current_start = 0
    current_messages: list[tuple[str, str, str]] = []

    for i, (log_type, message_json, timestamp) in enumerate(transcript_item_messages):
        is_prompt = False
        if log_type == "user":
            try:
                md = json.loads(message_json)
            except json.JSONDecodeError:
                md = {}
            content = md.get("content") if isinstance(md, dict) else None
            if isinstance(content, str) and content.strip():
                is_prompt = True

        if is_prompt and current_messages:
            groups.append(
                {
                    "start": current_start,
                    "end": i - 1,
                    "prompt": current_prompt,
                    "messages": current_messages,
                }
            )
            current_prompt = None
            current_start = i
            current_messages = []

        if is_prompt and current_prompt is None and log_type == "user":
            try:
                md = json.loads(message_json)
            except json.JSONDecodeError:
                md = {}
            content = md.get("content") if isinstance(md, dict) else None
            current_prompt = content.strip() if isinstance(content, str) and content.strip() else None

        current_messages.append((log_type, message_json, timestamp))

    if current_messages:
        groups.append(
            {
                "start": current_start,
                "end": len(transcript_item_messages) - 1,
                "prompt": current_prompt,
                "messages": current_messages,
            }
        )

    # Render group summaries.
    rendered_groups: list[dict[str, Any]] = []
    prompt_num = 0
    for group_idx, g in enumerate(groups):
        start_idx = int(g["start"])
        end_idx = int(g["end"])
        msgs = g["messages"]
        prompt_text = g.get("prompt")

        start_ts = transcript_item_timestamps[start_idx] if start_idx < len(transcript_item_timestamps) else ""
        end_ts = transcript_item_timestamps[end_idx] if end_idx < len(transcript_item_timestamps) else start_ts

        start_dt = _parse_rfc3339(start_ts) if isinstance(start_ts, str) else None
        end_dt = _parse_rfc3339(end_ts) if isinstance(end_ts, str) else None
        duration_ms: int | None = None
        if start_dt and end_dt:
            duration_ms = int((end_dt - start_dt).total_seconds() * 1000)

        stats_obj = analyze_conversation(msgs)
        tool_calls = sum(stats_obj.tool_counts.values())
        tool_stats_str = format_tool_stats(stats_obj.tool_counts)

        long_texts_html = ""
        if stats_obj.long_texts:
            parts: list[str] = []
            for text in stats_obj.long_texts[:1]:
                snippet = text
                if len(snippet) > 4000:
                    snippet = snippet[:4000] + "\n\n…"
                parts.append(get_template("macros.html").module.index_long_text(render_markdown_text(snippet)))
            long_texts_html = "".join(parts)

        prompt_html = ""
        prompt_raw = prompt_text.strip() if isinstance(prompt_text, str) and prompt_text.strip() else None
        if prompt_raw is not None:
            prompt_num += 1
            prompt_html = render_markdown_text(prompt_raw)
        else:
            prompt_html = "<em>(session start)</em>"

        prompt_plain = prompt_raw or "(session start)"
        prompt_plain = prompt_plain.replace("\n", " ").strip()
        if len(prompt_plain) > 160:
            prompt_plain = prompt_plain[:160] + "…"

        rendered_groups.append(
            {
                "group_index": group_idx,
                "display_label": f"#{prompt_num}" if prompt_raw is not None else "Start",
                "start": start_idx,
                "end": end_idx,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "duration_ms": duration_ms,
                "duration_label": _format_duration_ms(duration_ms),
                "message_count": (end_idx - start_idx + 1) if end_idx >= start_idx else 0,
                "tool_calls": tool_calls,
                "tool_stats": tool_stats_str,
                "long_texts_html": long_texts_html,
                "commit_count": len(stats_obj.commits),
                "prompt_html": prompt_html,
                "prompt_plain": prompt_plain,
                "prompt_raw": prompt_raw,
            }
        )

    task_duration_ms: list[int] = [
        int(g["duration_ms"])
        for g in rendered_groups
        if g.get("prompt_raw") is not None and isinstance(g.get("duration_ms"), int)
    ]
    task_time_summary = ""
    if task_duration_ms:
        total_ms = sum(task_duration_ms)
        avg_ms = int(total_ms / len(task_duration_ms))
        task_time_summary = (
            f"task time avg {_format_duration_ms(avg_ms)} · min {_format_duration_ms(min(task_duration_ms))} · "
            f"max {_format_duration_ms(max(task_duration_ms))}"
        )

    return groups, rendered_groups, task_time_summary


def generate_html_from_session_data(
    session_data: dict[str, Any],
    output_dir: str | Path,
    *,
    github_repo: str | None,
    stats: ParseStats | None = None,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings_html = _format_drift_warning_html(stats)

    (
        transcript_items_html,
        transcript_item_ids,
        transcript_item_kinds,
        transcript_item_timestamps,
        transcript_item_messages,
    ) = _collect_transcript_items(session_data, github_repo=github_repo, collect_html=True)

    chunk_paths = _write_transcript_chunks(
        output_dir=output_dir,
        items_html=transcript_items_html,
        chunk_size=TRANSCRIPT_CHUNK_SIZE,
    )

    _, rendered_groups, task_time_summary = _build_conversation_groups(
        transcript_item_messages, transcript_item_timestamps
    )

    kind_to_char = {"user": "u", "assistant": "a", "tool_call": "t", "tool_reply": "r", "system": "s"}
    kinds_compact = "".join(kind_to_char.get(k, "s") for k in transcript_item_kinds)

    viewer_meta = {
        "format": "codex-transcripts.viewer.v2",
        "total": len(transcript_items_html),
        "chunk_size": TRANSCRIPT_CHUNK_SIZE,
        "chunks": chunk_paths,
        "kinds": kinds_compact,
        "ids": transcript_item_ids,
        "ts": transcript_item_timestamps,
        "groups": [{"start": g["start"], "end": g["end"], "prompt": g.get("prompt_raw")} for g in rendered_groups],
    }

    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        warnings_html=warnings_html,
        meta_json=json.dumps(viewer_meta, ensure_ascii=False),
        groups=rendered_groups,
        total_messages=len(transcript_items_html),
        total_groups=len(rendered_groups),
        task_time_summary=task_time_summary,
    )
    (output_dir / "index.html").write_text(index_content, encoding="utf-8")


def generate_chat_html_from_session_data(
    session_data: dict[str, Any],
    output_dir: str | Path,
    *,
    github_repo: str | None,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _, _, _, transcript_item_timestamps, transcript_item_messages = _collect_transcript_items(
        session_data, github_repo=github_repo, collect_html=False
    )

    groups, rendered_groups, task_time_summary = _build_conversation_groups(
        transcript_item_messages, transcript_item_timestamps
    )

    chat_groups: list[dict[str, Any]] = []
    total_chat_messages = 0
    for idx, group in enumerate(groups):
        rendered = rendered_groups[idx]
        messages_html: list[str] = []
        meta_text = None
        meta_parts: list[str] = []
        tool_stats = rendered.get("tool_stats")
        if isinstance(tool_stats, str) and tool_stats.strip():
            meta_parts.append(tool_stats.strip())
        duration_label = rendered.get("duration_label")
        if isinstance(duration_label, str) and duration_label.strip() and duration_label != "-":
            meta_parts.append(duration_label.strip())
        if meta_parts:
            meta_text = " - ".join(meta_parts)
        for log_type, message_json, timestamp in group["messages"]:
            msg_html = render_chat_message(
                log_type,
                message_json,
                timestamp,
                github_repo,
                meta_text=meta_text if log_type == "assistant" else None,
            )
            if msg_html:
                messages_html.append(msg_html)
        total_chat_messages += len(messages_html)
        payload = dict(rendered)
        payload["messages_html"] = messages_html
        chat_groups.append(payload)

    template = get_template("chat.html")
    html = template.render(
        css=CSS,
        js=JS,
        chat_groups=chat_groups,
        total_messages=total_chat_messages,
        total_groups=len(rendered_groups),
        task_time_summary=task_time_summary,
    )
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def generate_html_from_rollout(
    rollout_path: str | Path,
    output_dir: str | Path,
    *,
    github_repo: str | None = None,
    include_json: bool = False,
    style: str = "viewer",
) -> tuple[Path, SessionMeta | None, ParseStats]:
    session_data, meta, stats = parse_rollout_file(
        rollout_path,
    )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if include_json:
        src = Path(rollout_path)
        dst = out_dir / src.name
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())

    # Prefer explicit github repo, but fall back to session meta git URL.
    if github_repo is None and meta and meta.git:
        from codex_transcripts.render import detect_github_repo_from_url

        github_repo = detect_github_repo_from_url(meta.git.get("repository_url"))

    if style not in {"viewer", "chat"}:
        raise ValueError(f"Unknown render style: {style}")

    if style == "chat":
        generate_chat_html_from_session_data(session_data, out_dir, github_repo=github_repo)
    else:
        generate_html_from_session_data(session_data, out_dir, github_repo=github_repo, stats=stats)
    return out_dir, meta, stats


def generate_json_from_rollout(
    rollout_path: str | Path,
    output_dir: str | Path,
    *,
    include_source: bool = False,
) -> tuple[Path, SessionMeta | None, ParseStats]:
    session_data, meta, stats = parse_rollout_file(
        rollout_path,
    )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if include_source:
        src = Path(rollout_path)
        dst = out_dir / src.name
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())

    out_path = out_dir / "transcript.json"
    payload = {
        "format": "codex-transcripts.session.v1",
        "source_path": str(Path(rollout_path).expanduser()),
        "meta": as_meta_dict(meta),
        "stats": asdict(stats),
        "session": session_data,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path, meta, stats


def open_output(output_dir: str | Path) -> None:
    index = Path(output_dir) / "index.html"
    webbrowser.open(index.resolve().as_uri())


def default_output_dir() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="codex-transcripts-"))
    return tmp


def output_auto_dir(parent: str | Path, *, session_id: str | None, filename: str) -> Path:
    parent = Path(parent)
    if session_id:
        return parent / f"session_{session_id}"
    safe = filename.replace(":", "-")
    return parent / safe


def as_meta_dict(meta: SessionMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return asdict(meta)


def generate_archive_index(
    output_root: str | Path,
    *,
    sessions: list[dict[str, Any]],
) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    template = get_template("archive_index.html")
    html = template.render(css=CSS, js=JS, sessions=sessions, total_sessions=len(sessions))
    path = output_root / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
