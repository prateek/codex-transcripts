from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


CODEX_SESSIONS_SUBDIR = "sessions"
CODEX_ARCHIVED_SESSIONS_SUBDIR = "archived_sessions"

ROLLOUT_FILENAME_RE = re.compile(
    r"^rollout-(?P<ts>.+)-(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\\.jsonl$"
)


class RolloutParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionMeta:
    id: str | None
    timestamp: str | None
    cwd: str | None
    originator: str | None
    cli_version: str | None
    instructions: str | None
    source: str | None
    model_provider: str | None
    git: dict[str, Any] | None


@dataclass
class ParseStats:
    total_lines: int = 0
    parsed_rollout_lines: int = 0
    skipped_lines: int = 0
    emitted_loglines: int = 0
    system_rollout_types: dict[str, int] = field(default_factory=dict)
    system_event_types: dict[str, int] = field(default_factory=dict)
    system_response_item_types: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionInfo:
    path: Path
    summary: str
    mtime: float


@dataclass(frozen=True)
class SessionRow:
    path: Path
    session_id: str | None
    preview: str
    created_at: datetime | None
    updated_at: datetime | None
    cwd: str | None
    git_branch: str | None
    source: str | None
    model_provider: str | None


@dataclass(frozen=True)
class ResumeStyleMetrics:
    max_updated_width: int
    max_branch_width: int
    max_cwd_width: int
    show_cwd: bool


def _parse_rfc3339(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    # Support both "...Z" and "...+00:00"
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def human_time_ago(ts: datetime) -> str:
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        n = max(secs, 0)
        return f"{n} second ago" if n == 1 else f"{n} seconds ago"
    if secs < 60 * 60:
        m = secs // 60
        return f"{m} minute ago" if m == 1 else f"{m} minutes ago"
    if secs < 60 * 60 * 24:
        h = secs // 3600
        return f"{h} hour ago" if h == 1 else f"{h} hours ago"
    d = secs // (60 * 60 * 24)
    return f"{d} day ago" if d == 1 else f"{d} days ago"


def _right_elide(s: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len == 1:
        return "…"
    return "…" + s[-(max_len - 1) :]


def _normalize_for_path_comparison(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path


def paths_match(a: str | Path, b: str | Path) -> bool:
    try:
        pa = _normalize_for_path_comparison(Path(a))
        pb = _normalize_for_path_comparison(Path(b))
        return pa == pb
    except TypeError:
        return str(a) == str(b)


def read_rollout_head(path: Path, *, max_records: int = 50) -> list[dict[str, Any]]:
    head: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if len(head) >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    head.append(obj)
    except OSError:
        return []
    return head


def _looks_like_environment_context(text: str) -> bool:
    t = text.strip()
    return t.startswith("<environment_context>") or t.startswith("<environment_context ")


def extract_preview_from_head(head: list[dict[str, Any]]) -> str | None:
    # Prefer user messages embedded as response_item message entries.
    for obj in head:
        if obj.get("type") != "response_item":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        text = extract_text_from_codex_content(payload.get("content"))
        if text and not _looks_like_environment_context(text):
            return text

    # Fall back to user_message events.
    for obj in head:
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "user_message":
            continue
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip() and not _looks_like_environment_context(msg):
            return msg.strip()

    return None


def extract_session_meta_from_head(head: list[dict[str, Any]]) -> SessionMeta | None:
    for obj in head:
        if obj.get("type") != "session_meta":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        return SessionMeta(
            id=str(payload.get("id")) if payload.get("id") is not None else None,
            timestamp=payload.get("timestamp"),
            cwd=str(payload.get("cwd")) if payload.get("cwd") is not None else None,
            originator=payload.get("originator"),
            cli_version=payload.get("cli_version"),
            instructions=payload.get("instructions"),
            source=payload.get("source"),
            model_provider=payload.get("model_provider"),
            git=payload.get("git") if isinstance(payload.get("git"), dict) else None,
        )
    return None


def list_session_rows(
    *,
    codex_home: str | Path | None = None,
    limit: int = 50,
    include_archived: bool = True,
    query: str | None = None,
    filter_cwd: Path | None = None,
) -> list[SessionRow]:
    q = query.strip().lower() if query and query.strip() else None
    candidates = list(iter_rollout_files(codex_home=codex_home, include_archived=include_archived))
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    rows: list[SessionRow] = []
    for path in candidates:
        if len(rows) >= limit:
            break
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc)

        head = read_rollout_head(path)
        meta = extract_session_meta_from_head(head)
        preview = extract_preview_from_head(head) or "(no message yet)"

        cwd = meta.cwd if meta else None
        session_id = get_session_id_from_filename(path)
        git_branch = None
        if meta and meta.git:
            b = meta.git.get("branch")
            git_branch = b if isinstance(b, str) else None

        if filter_cwd is not None:
            if cwd is None:
                continue
            if not paths_match(cwd, filter_cwd):
                continue

        if q is not None:
            haystacks: list[str] = [preview, str(path)]
            if cwd:
                haystacks.append(cwd)
            if git_branch:
                haystacks.append(git_branch)
            if session_id:
                haystacks.append(session_id)
            if not any(q in h.lower() for h in haystacks):
                continue

        created_at = _parse_rfc3339(meta.timestamp) if meta else None
        rows.append(
            SessionRow(
                path=path,
                session_id=session_id,
                preview=preview,
                created_at=created_at,
                updated_at=updated_at,
                cwd=cwd,
                git_branch=git_branch,
                source=meta.source if meta else None,
                model_provider=meta.model_provider if meta else None,
            )
        )

    return rows


def calculate_resume_style_metrics(rows: Sequence[SessionRow], *, show_cwd: bool) -> ResumeStyleMetrics:
    max_updated_width = len("Updated")
    max_branch_width = len("Branch")
    max_cwd_width = len("CWD") if show_cwd else 0

    for row in rows:
        updated_label = format_updated_label(row)
        branch_label = _right_elide(row.git_branch or "", 24)
        cwd_label = _right_elide(row.cwd or "", 24) if show_cwd else ""

        max_updated_width = max(max_updated_width, len(updated_label))
        max_branch_width = max(max_branch_width, len(branch_label))
        if show_cwd:
            max_cwd_width = max(max_cwd_width, len(cwd_label))

    return ResumeStyleMetrics(
        max_updated_width=max_updated_width,
        max_branch_width=max_branch_width,
        max_cwd_width=max_cwd_width,
        show_cwd=show_cwd,
    )


def format_updated_label(row: SessionRow) -> str:
    if row.updated_at is not None:
        return human_time_ago(row.updated_at)
    if row.created_at is not None:
        return human_time_ago(row.created_at)
    return "-"


def format_resume_style_header(metrics: ResumeStyleMetrics) -> str:
    parts = [
        f"{'Updated':<{metrics.max_updated_width}}",
        f"{'Branch':<{metrics.max_branch_width}}",
    ]
    if metrics.show_cwd:
        parts.append(f"{'CWD':<{metrics.max_cwd_width}}")
    parts.append("Conversation")
    return "  ".join(parts)


def format_resume_style_row(row: SessionRow, *, metrics: ResumeStyleMetrics) -> str:
    updated_label = format_updated_label(row)
    updated = f"{updated_label:<{metrics.max_updated_width}}"

    branch_label = _right_elide(row.git_branch or "", 24)
    branch_value = branch_label if branch_label else "-"
    branch = f"{branch_value:<{metrics.max_branch_width}}"

    preview = row.preview.replace("\n", " ").strip()
    preview = preview[:160] + ("…" if len(preview) > 160 else "")

    if metrics.show_cwd:
        cwd_label = _right_elide(row.cwd or "", 24)
        cwd_value = cwd_label if cwd_label else "-"
        cwd = f"{cwd_value:<{metrics.max_cwd_width}}"
        return f"{updated}  {branch}  {cwd}  {preview}"

    return f"{updated}  {branch}  {preview}"


def get_codex_home(codex_home: str | Path | None = None) -> Path:
    raw = (
        str(codex_home)
        if codex_home is not None
        else os.environ.get("CODEX_HOME", "~/.codex")
    )
    return Path(raw).expanduser().resolve()


def iter_rollout_files(*, codex_home: str | Path | None = None, include_archived: bool) -> Iterator[Path]:
    home = get_codex_home(codex_home)
    sessions_dir = home / CODEX_SESSIONS_SUBDIR
    if sessions_dir.exists():
        yield from sessions_dir.rglob("rollout-*.jsonl")
    if include_archived:
        archived_dir = home / CODEX_ARCHIVED_SESSIONS_SUBDIR
        if archived_dir.exists():
            yield from archived_dir.rglob("rollout-*.jsonl")


def find_local_sessions(
    *,
    codex_home: str | Path | None = None,
    limit: int = 10,
    include_archived: bool = True,
) -> list[SessionInfo]:
    candidates = list(iter_rollout_files(codex_home=codex_home, include_archived=include_archived))
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    results: list[SessionInfo] = []
    for path in candidates:
        if len(results) >= limit:
            break
        summary = get_session_summary(path)
        if summary == "(no summary)" or summary.strip().lower() == "warmup":
            continue
        results.append(SessionInfo(path=path, summary=summary, mtime=path.stat().st_mtime))
    return results


def get_session_id_from_filename(path: Path) -> str | None:
    match = ROLLOUT_FILENAME_RE.match(path.name)
    if not match:
        return None
    return match.group("uuid")


def get_session_summary(filepath: str | Path, max_length: int = 200) -> str:
    path = Path(filepath)
    try:
        for obj in _iter_rollout_objects(path):
            rollout_type = obj.get("type")
            payload = obj.get("payload")
            if rollout_type == "event_msg" and isinstance(payload, dict):
                if payload.get("type") == "user_message":
                    msg = payload.get("message")
                    if isinstance(msg, str) and msg.strip():
                        msg = msg.strip()
                        return msg if len(msg) <= max_length else msg[: max_length - 3] + "..."
            if rollout_type == "response_item" and isinstance(payload, dict):
                if payload.get("type") == "message" and payload.get("role") == "user":
                    text = extract_text_from_codex_content(payload.get("content"))
                    if text:
                        return text if len(text) <= max_length else text[: max_length - 3] + "..."
        return "(no summary)"
    except Exception:
        return "(no summary)"


def extract_text_from_codex_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return " ".join(parts).strip()


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _maybe_parse_json(s: Any) -> Any:
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _iter_rollout_objects(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "loglines" in data:
            # Already normalized (primarily for tests / interoperability).
            return []
        if isinstance(data, list):
            return [obj for obj in data if isinstance(obj, dict)]
        return []

    objects: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


@dataclass(frozen=True)
class _ParsedLogline:
    kind: str
    logline: dict[str, Any] | None


def _system_record_logline(*, timestamp: str, label: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "system",
        "timestamp": timestamp,
        "message": {
            "role": "system",
            "content": [
                {
                    "type": "system_record",
                    "label": label,
                    "record": record,
                }
            ],
        },
    }


def parse_rollout_file(
    filepath: str | Path,
) -> tuple[dict[str, Any], SessionMeta | None, ParseStats]:
    path = Path(filepath)
    stats = ParseStats()
    records: list[_ParsedLogline] = []
    meta: SessionMeta | None = None

    saw_event_messages = False

    for obj in _iter_rollout_objects(path):
        stats.total_lines += 1

        timestamp = obj.get("timestamp")
        rollout_type = obj.get("type")
        payload = obj.get("payload")

        if not isinstance(timestamp, str) or not isinstance(rollout_type, str):
            stats.skipped_lines += 1
            continue

        stats.parsed_rollout_lines += 1

        if rollout_type == "session_meta" and isinstance(payload, dict):
            if meta is None:
                meta = SessionMeta(
                    id=str(payload.get("id")) if payload.get("id") is not None else None,
                    timestamp=payload.get("timestamp"),
                    cwd=str(payload.get("cwd")) if payload.get("cwd") is not None else None,
                    originator=payload.get("originator"),
                    cli_version=payload.get("cli_version"),
                    instructions=payload.get("instructions"),
                    source=payload.get("source"),
                    model_provider=payload.get("model_provider"),
                    git=payload.get("git") if isinstance(payload.get("git"), dict) else None,
                )
            continue

        if rollout_type == "event_msg" and isinstance(payload, dict):
            event_type = payload.get("type")

            if event_type in {"user_message", "agent_message"}:
                saw_event_messages = True

            if event_type == "context_compacted":
                records.append(
                    _ParsedLogline(
                        kind="event_context_compacted",
                        logline={
                            "type": "system",
                            "timestamp": timestamp,
                            "message": {
                                "role": "system",
                                "content": [
                                    {"type": "text", "text": "**Context compacted**"},
                                ],
                            },
                        },
                    )
                )
                continue

            if event_type == "user_message":
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    records.append(
                        _ParsedLogline(
                            kind="event_user_message",
                            logline={
                                "type": "user",
                                "timestamp": timestamp,
                                "message": {"role": "user", "content": msg},
                            },
                        )
                    )
                continue

            if event_type == "agent_message":
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    records.append(
                        _ParsedLogline(
                            kind="event_agent_message",
                            logline={
                                "type": "assistant",
                                "timestamp": timestamp,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": msg}],
                                },
                            },
                        )
                    )
                continue

            if event_type == "turn_aborted":
                reason = payload.get("reason")
                reason_str = reason if isinstance(reason, str) and reason.strip() else None
                suffix = f" ({reason_str})" if reason_str else ""
                records.append(
                    _ParsedLogline(
                        kind="event_turn_aborted",
                        logline={
                            "type": "system",
                            "timestamp": timestamp,
                            "message": {
                                "role": "system",
                                "content": [{"type": "text", "text": f"**Turn aborted**{suffix}"}],
                            },
                        },
                    )
                )
                continue

            if event_type == "agent_reasoning":
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    records.append(
                        _ParsedLogline(
                            kind="event_agent_reasoning",
                            logline={
                                "type": "system",
                                "timestamp": timestamp,
                                "message": {
                                    "role": "system",
                                    "content": [{"type": "thinking", "thinking": text}],
                                },
                            },
                        )
                    )
                continue

            if event_type == "agent_reasoning_raw_content":
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    records.append(
                        _ParsedLogline(
                            kind="event_agent_reasoning_raw",
                            logline={
                                "type": "system",
                                "timestamp": timestamp,
                                "message": {
                                    "role": "system",
                                    "content": [{"type": "thinking", "thinking": text}],
                                },
                            },
                        )
                    )
                continue

            if event_type == "token_count":
                records.append(
                    _ParsedLogline(
                        kind="event_token_count",
                        logline={
                            "type": "system",
                            "timestamp": timestamp,
                            "message": {
                                "role": "system",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": "token_count",
                                        "input": payload,
                                        "id": "",
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if isinstance(event_type, str):
                _bump(stats.system_event_types, event_type)
                records.append(
                    _ParsedLogline(
                        kind="system_event_msg",
                        logline=_system_record_logline(
                            timestamp=timestamp,
                            label=f"event_msg:{event_type}",
                            record=obj,
                        ),
                    )
                )
            else:
                _bump(stats.system_event_types, "(missing)")
                records.append(
                    _ParsedLogline(
                        kind="system_event_msg",
                        logline=_system_record_logline(
                            timestamp=timestamp,
                            label="event_msg:(missing)",
                            record=obj,
                        ),
                    )
                )
            continue

        if rollout_type == "response_item" and isinstance(payload, dict):
            item_type = payload.get("type")

            if item_type == "function_call":
                call_id = payload.get("call_id") or ""
                name = payload.get("name") or "function_call"
                arguments = payload.get("arguments") or ""
                input_obj = _maybe_parse_json(arguments)
                if input_obj is None:
                    input_obj = {"arguments": arguments}
                records.append(
                    _ParsedLogline(
                        kind="tool_use",
                        logline={
                            "type": "assistant",
                            "timestamp": timestamp,
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": name,
                                        "input": input_obj,
                                        "id": call_id,
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "custom_tool_call":
                call_id = payload.get("call_id") or ""
                name = payload.get("name") or "custom_tool_call"
                raw_input = payload.get("input")
                input_obj = _maybe_parse_json(raw_input)
                if input_obj is None:
                    input_obj = {"input": raw_input}
                records.append(
                    _ParsedLogline(
                        kind="tool_use",
                        logline={
                            "type": "assistant",
                            "timestamp": timestamp,
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": name,
                                        "input": input_obj,
                                        "id": call_id,
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "local_shell_call":
                call_id = payload.get("call_id") or ""
                input_obj = {k: v for k, v in payload.items() if k != "id"}
                records.append(
                    _ParsedLogline(
                        kind="tool_use",
                        logline={
                            "type": "assistant",
                            "timestamp": timestamp,
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": "local_shell_call",
                                        "input": input_obj,
                                        "id": call_id,
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "web_search_call":
                input_obj = {k: v for k, v in payload.items() if k != "id"}
                records.append(
                    _ParsedLogline(
                        kind="tool_use",
                        logline={
                            "type": "assistant",
                            "timestamp": timestamp,
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": "web_search_call",
                                        "input": input_obj,
                                        "id": payload.get("id") or "",
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "function_call_output":
                call_id = payload.get("call_id") or ""
                output = payload.get("output")
                is_error = False
                if isinstance(output, dict) and output.get("success") is False:
                    is_error = True
                records.append(
                    _ParsedLogline(
                        kind="tool_result",
                        logline={
                            "type": "user",
                            "timestamp": timestamp,
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "content": output,
                                        "is_error": is_error,
                                        "tool_use_id": call_id,
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "custom_tool_call_output":
                call_id = payload.get("call_id") or ""
                output = payload.get("output")
                records.append(
                    _ParsedLogline(
                        kind="tool_result",
                        logline={
                            "type": "user",
                            "timestamp": timestamp,
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "content": output,
                                        "is_error": False,
                                        "tool_use_id": call_id,
                                    }
                                ],
                            },
                        },
                    )
                )
                continue

            if item_type == "message":
                role = payload.get("role")
                text = extract_text_from_codex_content(payload.get("content"))
                if role == "user" and text:
                    records.append(
                        _ParsedLogline(
                            kind="response_user_message",
                            logline={
                                "type": "user",
                                "timestamp": timestamp,
                                "message": {"role": "user", "content": text},
                            },
                        )
                    )
                elif role == "assistant" and text:
                    records.append(
                        _ParsedLogline(
                            kind="response_assistant_message",
                            logline={
                                "type": "assistant",
                                "timestamp": timestamp,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": text}],
                                },
                            },
                        )
                    )
                continue

            if item_type == "reasoning":
                summary = payload.get("summary")
                if summary is not None:
                    records.append(
                        _ParsedLogline(
                            kind="response_reasoning",
                            logline={
                                "type": "system",
                                "timestamp": timestamp,
                                "message": {
                                    "role": "system",
                                    "content": [
                                        {
                                            "type": "thinking",
                                            "thinking": json.dumps(summary, ensure_ascii=False),
                                        }
                                    ],
                                },
                            },
                        )
                    )
                continue

            if isinstance(item_type, str):
                _bump(stats.system_response_item_types, item_type)
                records.append(
                    _ParsedLogline(
                        kind="system_response_item",
                        logline=_system_record_logline(
                            timestamp=timestamp,
                            label=f"response_item:{item_type}",
                            record=obj,
                        ),
                    )
                )
            else:
                _bump(stats.system_response_item_types, "(missing)")
                records.append(
                    _ParsedLogline(
                        kind="system_response_item",
                        logline=_system_record_logline(
                            timestamp=timestamp,
                            label="response_item:(missing)",
                            record=obj,
                        ),
                    )
                )
            continue

        if rollout_type == "compacted" and isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                records.append(
                    _ParsedLogline(
                        kind="compacted",
                        logline={
                            "type": "system",
                            "timestamp": timestamp,
                            "message": {
                                "role": "system",
                                "content": [{"type": "thinking", "thinking": message}],
                            },
                        },
                    )
                )
            continue

        if rollout_type == "turn_context" and isinstance(payload, dict):
            records.append(
                _ParsedLogline(
                    kind="turn_context",
                    logline={
                        "type": "system",
                        "timestamp": timestamp,
                        "message": {
                            "role": "system",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "turn_context",
                                    "input": payload,
                                    "id": "",
                                }
                            ],
                        },
                    },
                )
            )
            continue

        _bump(stats.system_rollout_types, rollout_type)
        records.append(
            _ParsedLogline(
                kind="system_rollout_type",
                logline=_system_record_logline(
                    timestamp=timestamp,
                    label=f"rollout:{rollout_type}",
                    record=obj,
                ),
            )
        )

    if saw_event_messages:
        filtered = [
            r.logline
            for r in records
            if r.logline is not None
            and r.kind not in {"response_user_message", "response_assistant_message"}
        ]
    else:
        filtered = [r.logline for r in records if r.logline is not None]

    stats.emitted_loglines = len(filtered)
    if not filtered:
        raise RolloutParseError("no usable messages found in rollout file")

    return {"loglines": filtered}, meta, stats
