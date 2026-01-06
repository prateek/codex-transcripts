from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any

from jinja2 import Environment, PackageLoader
import markdown


_jinja_env = Environment(
    loader=PackageLoader("codex_transcripts", "templates"),
    autoescape=True,
)

_macros_template = _jinja_env.get_template("macros.html")
_macros = _macros_template.module


def get_template(name: str):
    return _jinja_env.get_template(name)


COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")
GITHUB_REPO_FROM_URL = re.compile(
    r"(?:github\\.com[:/])(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\\.git)?/?$"
)

PROMPTS_PER_PAGE = 5
LONG_TEXT_THRESHOLD = 300


def format_json(obj: Any) -> str:
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        return f'<pre class="json">{html.escape(formatted)}</pre>'
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{html.escape(str(obj))}</pre>"


def render_markdown_text(text: str | None) -> str:
    if not text:
        return ""
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text: Any) -> bool:
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    )


def detect_github_repo_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = GITHUB_REPO_FROM_URL.search(url.strip())
    if not match:
        return None
    return match.group("repo")


def detect_github_repo_from_session_meta(meta: dict[str, Any] | None) -> str | None:
    if not meta:
        return None
    git = meta.get("git")
    if not isinstance(git, dict):
        return None
    return detect_github_repo_from_url(git.get("repository_url"))


def render_todo_write(tool_input: dict[str, Any], tool_id: str) -> str:
    todos = tool_input.get("todos", [])
    if not todos:
        return ""
    return _macros.todo_list(todos, tool_id)


def render_write_tool(tool_input: dict[str, Any], tool_id: str) -> str:
    file_path = tool_input.get("file_path", "Unknown file")
    content = tool_input.get("content", "")
    return _macros.write_tool(file_path, content, tool_id)


def render_edit_tool(tool_input: dict[str, Any], tool_id: str) -> str:
    file_path = tool_input.get("file_path", "Unknown file")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = tool_input.get("replace_all", False)
    return _macros.edit_tool(file_path, old_string, new_string, replace_all, tool_id)


def render_bash_tool(tool_input: dict[str, Any], tool_id: str) -> str:
    command = tool_input.get("command", "")
    description = tool_input.get("description", "")
    return _macros.bash_tool(command, description, tool_id)


def _codex_tool_alias(name: str) -> str:
    # Codex CLI harness tool names are often fully-qualified.
    if name.startswith("functions."):
        return name.removeprefix("functions.")
    return name


def render_content_block(block: Any, github_repo: str | None) -> str:
    if not isinstance(block, dict):
        return f"<p>{html.escape(str(block))}</p>"
    block_type = block.get("type", "")

    if block_type == "image":
        source = block.get("source", {})
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        return _macros.image_block(media_type, data)

    if block_type == "thinking":
        content_html = render_markdown_text(block.get("thinking", ""))
        return _macros.thinking(content_html)

    if block_type == "text":
        content_html = render_markdown_text(block.get("text", ""))
        return _macros.assistant_text(content_html)

    if block_type == "tool_use":
        tool_name = block.get("name", "Unknown tool")
        tool_input = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
        tool_id = block.get("id", "")
        alias = _codex_tool_alias(tool_name)

        # Special-cases for Codex-harness tool shapes.
        if alias == "exec_command":
            cmd = tool_input.get("cmd") or tool_input.get("command") or ""
            desc = tool_input.get("justification") or tool_input.get("description") or ""
            return _macros.bash_tool(cmd, desc, tool_id)

        if alias == "update_plan":
            return _macros.tool_use(alias, "", json.dumps(tool_input, indent=2, ensure_ascii=False), tool_id)

        if alias == "apply_patch":
            patch = tool_input.get("patch")
            if isinstance(patch, str):
                return _macros.tool_use(
                    alias,
                    "",
                    json.dumps({"patch": patch}, indent=2, ensure_ascii=False),
                    tool_id,
                )

        if alias == "todo_write":
            return render_todo_write(tool_input, tool_id)

        if alias == "write":
            return render_write_tool(tool_input, tool_id)

        if alias == "edit":
            return render_edit_tool(tool_input, tool_id)

        if alias == "bash":
            return render_bash_tool(tool_input, tool_id)

        description = tool_input.get("description", "")
        display_input = {k: v for k, v in tool_input.items() if k != "description"}
        input_json = json.dumps(display_input, indent=2, ensure_ascii=False)
        return _macros.tool_use(tool_name, description, input_json, tool_id)

    if block_type == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)

        if isinstance(content, str):
            commits_found = list(COMMIT_PATTERN.finditer(content))
            if commits_found:
                parts: list[str] = []
                last_end = 0
                for match in commits_found:
                    before = content[last_end : match.start()].strip()
                    if before:
                        parts.append(f"<pre>{html.escape(before)}</pre>")

                    commit_hash = match.group(1)
                    commit_msg = match.group(2)
                    parts.append(_macros.commit_card(commit_hash, commit_msg, github_repo))
                    last_end = match.end()

                after = content[last_end:].strip()
                if after:
                    parts.append(f"<pre>{html.escape(after)}</pre>")

                content_html = "".join(parts)
            else:
                content_html = f"<pre>{html.escape(content)}</pre>"
        elif isinstance(content, list) or is_json_like(content):
            content_html = format_json(content)
        else:
            content_html = format_json(content)
        return _macros.tool_result(content_html, is_error)

    if block_type == "system_record":
        label = block.get("label") if isinstance(block.get("label"), str) else "system"
        record = block.get("record")
        try:
            record_json = json.dumps(record, indent=2, ensure_ascii=False)
        except TypeError:
            record_json = json.dumps({"record": str(record)}, indent=2, ensure_ascii=False)
        return _macros.system_record(label, record_json)

    return format_json(block)


def is_tool_result_message(message_data: dict[str, Any]) -> bool:
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return False
    if not content:
        return False
    return all(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def render_user_message_content(message_data: dict[str, Any], github_repo: str | None) -> str:
    content = message_data.get("content", "")
    if isinstance(content, str):
        if is_json_like(content):
            return _macros.user_content(format_json(content))
        return _macros.user_content(render_markdown_text(content))
    if isinstance(content, list):
        return "".join(render_content_block(block, github_repo) for block in content)
    return f"<p>{html.escape(str(content))}</p>"


def render_assistant_message(message_data: dict[str, Any], github_repo: str | None) -> str:
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return f"<p>{html.escape(str(content))}</p>"
    return "".join(render_content_block(block, github_repo) for block in content)


def make_msg_id(timestamp: str) -> str:
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


@dataclass(frozen=True)
class ConversationStats:
    tool_counts: dict[str, int]
    long_texts: list[str]
    commits: list[tuple[str, str, str]]


def analyze_conversation(messages: list[tuple[str, str, str]]) -> ConversationStats:
    tool_counts: dict[str, int] = {}
    long_texts: list[str] = []
    commits: list[tuple[str, str, str]] = []

    for _log_type, message_json, timestamp in messages:
        if not message_json:
            continue
        try:
            message_data = json.loads(message_json)
        except json.JSONDecodeError:
            continue

        content = message_data.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "tool_use":
                tool_name = block.get("name", "Unknown")
                if not isinstance(tool_name, str):
                    tool_name = "Unknown"
                tool_name = _codex_tool_alias(tool_name)
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif block_type == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    for match in COMMIT_PATTERN.finditer(result_content):
                        commits.append((match.group(1), match.group(2), timestamp))
            elif block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and len(text) >= LONG_TEXT_THRESHOLD:
                    long_texts.append(text)

    return ConversationStats(tool_counts=tool_counts, long_texts=long_texts, commits=commits)


def format_tool_stats(tool_counts: dict[str, int]) -> str:
    if not tool_counts:
        return ""
    parts: list[str] = []
    for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        parts.append(f"{count} {name}")
    return " · ".join(parts)


def render_message(log_type: str, message_json: str, timestamp: str, github_repo: str | None) -> str:
    if not message_json:
        return ""
    try:
        message_data = json.loads(message_json)
    except json.JSONDecodeError:
        return ""

    if log_type == "user":
        content_html = render_user_message_content(message_data, github_repo)
        if is_tool_result_message(message_data):
            role_class, role_label = "tool-reply", "Tool reply"
        else:
            role_class, role_label = "user", "User"
    elif log_type == "assistant":
        content_html = render_assistant_message(message_data, github_repo)
        role_class, role_label = "assistant", "Assistant"
    elif log_type == "system":
        content_html = render_assistant_message(message_data, github_repo)
        role_class, role_label = "system", "System"
    else:
        return ""

    if not content_html.strip():
        return ""
    msg_id = make_msg_id(timestamp)
    return _macros.message(role_class, role_label, msg_id, timestamp, content_html)


# CSS / JS are borrowed from claude-code-transcripts and intentionally embedded so
# output is standalone (no external assets required).
CSS = """
:root {
  color-scheme: light;
  --bg-color: #f5f5f5;
  --card-bg: #ffffff;
  --user-bg: #e3f2fd;
  --user-border: #1976d2;
  --assistant-bg: #f5f5f5;
  --assistant-border: #9e9e9e;
  --thinking-bg: #fff8e1;
  --thinking-border: #ffc107;
  --thinking-text: #666;
  --tool-bg: #f3e5f5;
  --tool-border: #9c27b0;
  --tool-result-bg: #e8f5e9;
  --tool-error-bg: #ffebee;
  --text-color: #212121;
  --text-muted: #757575;
  --code-bg: #263238;
  --code-text: #aed581;

  --shadow-color: rgba(0,0,0,0.1);
  --border-subtle: rgba(0,0,0,0.06);
  --border: rgba(0,0,0,0.12);
  --surface-bg: rgba(0,0,0,0.03);
  --surface-border: rgba(0,0,0,0.1);
  --hover-bg: rgba(0,0,0,0.05);
  --inline-code-bg: rgba(0,0,0,0.08);

  --control-bg: #ffffff;
  --control-bg-hover: #f3f4f6;
  --control-border: rgba(0,0,0,0.2);
  --modal-backdrop: rgba(0,0,0,0.4);

  --bash-grad-from: #f3e5f5;
  --bash-grad-to: #e8eaf6;
  --bash-border: #7e57c2;

  --write-grad-from: #e3f2fd;
  --write-grad-to: #e8f5e9;
  --write-border: #4caf50;
  --write-header: #2e7d32;
  --write-truncate-fade: #e6f4ea;

  --edit-grad-from: #fff3e0;
  --edit-grad-to: #fce4ec;
  --edit-border: #ff9800;
  --edit-header: #e65100;
  --edit-truncate-fade: #fff0e5;

  --todo-grad-from: #e8f5e9;
  --todo-grad-to: #f1f8e9;
  --todo-border: #81c784;
  --todo-header: #2e7d32;

  --index-commit-border: #4caf50;

  --system-bg: #fff7ed;
  --system-border: #f97316;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    color-scheme: dark;
    --bg-color: #0b0f14;
    --card-bg: #111827;
    --user-bg: #0b2a3d;
    --user-border: #38bdf8;
    --assistant-bg: #0f172a;
    --assistant-border: #64748b;
    --thinking-bg: #2a240f;
    --thinking-border: #fbbf24;
    --thinking-text: #fcd34d;
    --tool-bg: #25152d;
    --tool-border: #c084fc;
    --tool-result-bg: #0f2418;
    --tool-error-bg: #2b1215;
    --text-color: #e5e7eb;
    --text-muted: #a1a1aa;
    --code-bg: #0b1020;
    --code-text: #a7f3d0;

    --shadow-color: rgba(0,0,0,0.6);
    --border-subtle: rgba(255,255,255,0.10);
    --border: rgba(255,255,255,0.18);
    --surface-bg: rgba(255,255,255,0.05);
    --surface-border: rgba(255,255,255,0.12);
    --hover-bg: rgba(255,255,255,0.08);
    --inline-code-bg: rgba(255,255,255,0.10);

    --control-bg: rgba(255,255,255,0.04);
    --control-bg-hover: rgba(255,255,255,0.08);
    --control-border: rgba(255,255,255,0.18);
    --modal-backdrop: rgba(0,0,0,0.55);

    --bash-grad-from: #1f1030;
    --bash-grad-to: #0f172a;
    --bash-border: #a78bfa;

    --write-grad-from: #0b2a3d;
    --write-grad-to: #0f2418;
    --write-border: #4ade80;
    --write-header: #4ade80;
    --write-truncate-fade: rgba(15,36,24,0.95);

    --edit-grad-from: #2a240f;
    --edit-grad-to: #2b1215;
    --edit-border: #fb923c;
    --edit-header: #fb923c;
    --edit-truncate-fade: rgba(42,18,21,0.95);

    --todo-grad-from: #0f2418;
    --todo-grad-to: #132a1d;
    --todo-border: #86efac;
    --todo-header: #86efac;

    --index-commit-border: #4ade80;

    --system-bg: #2b1a0f;
    --system-border: #fb923c;
  }
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg-color: #0b0f14;
  --card-bg: #111827;
  --user-bg: #0b2a3d;
  --user-border: #38bdf8;
  --assistant-bg: #0f172a;
  --assistant-border: #64748b;
  --thinking-bg: #2a240f;
  --thinking-border: #fbbf24;
  --thinking-text: #fcd34d;
  --tool-bg: #25152d;
  --tool-border: #c084fc;
  --tool-result-bg: #0f2418;
  --tool-error-bg: #2b1215;
  --text-color: #e5e7eb;
  --text-muted: #a1a1aa;
  --code-bg: #0b1020;
  --code-text: #a7f3d0;

  --shadow-color: rgba(0,0,0,0.6);
  --border-subtle: rgba(255,255,255,0.10);
  --border: rgba(255,255,255,0.18);
  --surface-bg: rgba(255,255,255,0.05);
  --surface-border: rgba(255,255,255,0.12);
  --hover-bg: rgba(255,255,255,0.08);
  --inline-code-bg: rgba(255,255,255,0.10);

  --control-bg: rgba(255,255,255,0.04);
  --control-bg-hover: rgba(255,255,255,0.08);
  --control-border: rgba(255,255,255,0.18);
  --modal-backdrop: rgba(0,0,0,0.55);

  --bash-grad-from: #1f1030;
  --bash-grad-to: #0f172a;
  --bash-border: #a78bfa;

  --write-grad-from: #0b2a3d;
  --write-grad-to: #0f2418;
  --write-border: #4ade80;
  --write-header: #4ade80;
  --write-truncate-fade: rgba(15,36,24,0.95);

  --edit-grad-from: #2a240f;
  --edit-grad-to: #2b1215;
  --edit-border: #fb923c;
  --edit-header: #fb923c;
  --edit-truncate-fade: rgba(42,18,21,0.95);

  --todo-grad-from: #0f2418;
  --todo-grad-to: #132a1d;
  --todo-border: #86efac;
  --todo-header: #86efac;

  --index-commit-border: #4ade80;

  --system-bg: #2b1a0f;
  --system-border: #fb923c;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; padding: 16px; line-height: 1.6; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 24px; padding-bottom: 8px; border-bottom: 2px solid var(--user-border); }
.header-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; border-bottom: 2px solid var(--user-border); padding-bottom: 8px; margin-bottom: 24px; }
.header-row h1 { border-bottom: none; padding-bottom: 0; margin-bottom: 0; flex: 1; min-width: 200px; }
.header-controls { display: flex; align-items: center; gap: 8px; }
.theme-toggle { padding: 8px; border: 1px solid var(--control-border); border-radius: 8px; background: var(--control-bg); cursor: pointer; display: flex; align-items: center; justify-content: center; color: var(--text-muted); }
.theme-toggle:hover { background: var(--control-bg-hover); }
.theme-toggle svg { display: none; }
.theme-toggle .icon-sun { display: inline; }
@media (prefers-color-scheme: dark) { :root:not([data-theme]) .theme-toggle .icon-sun { display: none; } :root:not([data-theme]) .theme-toggle .icon-moon { display: inline; } }
:root[data-theme="dark"] .theme-toggle .icon-sun { display: none; }
:root[data-theme="dark"] .theme-toggle .icon-moon { display: inline; }
.system-records-notice { background: var(--system-bg); border: 1px solid color-mix(in srgb, var(--system-border) 35%, transparent); border-left: 4px solid var(--system-border); border-radius: 12px; padding: 12px 16px; margin: 16px 0 24px 0; }
.system-records-notice-title { font-weight: 600; color: var(--system-border); margin-bottom: 4px; }
.system-records-notice-subtitle { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 8px; }
.system-records-notice details summary { cursor: pointer; font-size: 0.85rem; color: var(--system-border); }
.system-records-notice-section { margin-top: 12px; }
.system-records-notice-section-title { font-size: 0.85rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.4px; }
.message { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px var(--shadow-color); background: var(--card-bg); }
.message-header { padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; color: var(--text-muted); border-bottom: 1px solid var(--border-subtle); gap: 12px; }
.role-label { font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; font-size: 0.75rem; }
.timestamp-link { color: inherit; text-decoration: none; font-family: monospace; font-size: 0.8rem; padding: 2px 6px; border-radius: 4px; transition: background 0.2s; }
.timestamp-link:hover { background: var(--hover-bg); }
.message-content { padding: 16px; }
.message.user { background: var(--user-bg); border-left: 4px solid var(--user-border); }
.message.assistant { background: var(--assistant-bg); border-left: 4px solid var(--assistant-border); }
.message.tool-reply { background: var(--thinking-bg); border-left: 4px solid var(--thinking-border); }
.thinking { background: rgba(255,193,7,0.1); border: 1px solid rgba(255,193,7,0.3); border-radius: 8px; padding: 12px; margin: 12px 0; }
.thinking-label { font-weight: 600; color: var(--thinking-text); margin-bottom: 8px; font-size: 0.85rem; }
.tool-use { background: var(--tool-bg); border: 1px solid rgba(156,39,176,0.3); border: 1px solid color-mix(in srgb, var(--tool-border) 35%, transparent); border-radius: 8px; padding: 12px; margin: 12px 0; }
.tool-header { font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; color: var(--tool-border); font-size: 0.95rem; }
.tool-icon { font-size: 1rem; }
.tool-description { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 8px; }
.tool-result { background: var(--tool-result-bg); border: 1px solid rgba(76,175,80,0.3); border: 1px solid color-mix(in srgb, var(--write-border) 35%, transparent); border-radius: 8px; padding: 12px; margin: 12px 0; }
.tool-result.tool-error { background: var(--tool-error-bg); border-color: rgba(244,67,54,0.3); border-color: color-mix(in srgb, #f44336 35%, transparent); }
.commit-card { background: var(--surface-bg); border: 1px solid var(--surface-border); border-radius: 8px; padding: 10px 12px; margin: 10px 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
.commit-card a { text-decoration: none; color: inherit; }
.commit-card-hash { font-family: monospace; background: var(--inline-code-bg); padding: 2px 6px; border-radius: 4px; margin-right: 8px; font-size: 0.85rem; }
.bash-tool { background: linear-gradient(135deg, var(--bash-grad-from) 0%, var(--bash-grad-to) 100%); border: 1px solid var(--bash-border); }
.bash-command { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.85rem; }
.file-tool { border-radius: 8px; padding: 12px; margin: 12px 0; }
.write-tool { background: linear-gradient(135deg, var(--write-grad-from) 0%, var(--write-grad-to) 100%); border: 1px solid var(--write-border); }
.edit-tool { background: linear-gradient(135deg, var(--edit-grad-from) 0%, var(--edit-grad-to) 100%); border: 1px solid var(--edit-border); }
.file-tool-header { font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.write-header { color: var(--write-header); }
.edit-header { color: var(--edit-header); }
.file-tool-icon { font-size: 1rem; }
.file-tool-path { font-family: monospace; background: var(--inline-code-bg); padding: 2px 8px; border-radius: 4px; }
.file-tool-fullpath { font-family: monospace; font-size: 0.8rem; color: var(--text-muted); margin-bottom: 8px; word-break: break-all; }
.file-content { margin: 0; }
.edit-section { display: flex; margin: 4px 0; border-radius: 4px; overflow: hidden; }
.edit-label { padding: 8px 12px; font-weight: bold; font-family: monospace; display: flex; align-items: flex-start; }
.edit-old { background: #fce4ec; }
.edit-old .edit-label { color: #b71c1c; background: #f8bbd9; }
.edit-old .edit-content { color: #880e4f; }
.edit-new { background: #e8f5e9; }
.edit-new .edit-label { color: #1b5e20; background: #a5d6a7; }
.edit-new .edit-content { color: #1b5e20; }
.edit-content { margin: 0; flex: 1; background: transparent; font-size: 0.85rem; }
.edit-replace-all { font-size: 0.75rem; font-weight: normal; color: var(--text-muted); }
.write-tool .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--write-truncate-fade)); }
.edit-tool .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--edit-truncate-fade)); }
.todo-list { background: linear-gradient(135deg, var(--todo-grad-from) 0%, var(--todo-grad-to) 100%); border: 1px solid var(--todo-border); border-radius: 8px; padding: 12px; margin: 12px 0; }
.todo-header { font-weight: 600; color: var(--todo-header); margin-bottom: 10px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.todo-items { list-style: none; margin: 0; padding: 0; }
.todo-item { display: flex; align-items: flex-start; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border-subtle); font-size: 0.9rem; }
.todo-item:last-child { border-bottom: none; }
.todo-icon { flex-shrink: 0; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-weight: bold; border-radius: 50%; }
.todo-completed .todo-icon { color: #2e7d32; background: rgba(46, 125, 50, 0.15); }
.todo-completed .todo-content { color: #558b2f; text-decoration: line-through; }
.todo-in-progress .todo-icon { color: #f57c00; background: rgba(245, 124, 0, 0.15); }
.todo-in-progress .todo-content { color: #e65100; font-weight: 500; }
.todo-pending .todo-icon { color: var(--text-muted); background: var(--hover-bg); }
.todo-pending .todo-content { color: var(--text-muted); }
pre { background: var(--code-bg); color: var(--code-text); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; line-height: 1.5; margin: 8px 0; white-space: pre-wrap; word-wrap: break-word; }
pre.json { color: #e0e0e0; }
code { background: var(--inline-code-bg); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
pre code { background: none; padding: 0; }
.user-content { margin: 0; }
.truncatable { position: relative; }
.truncatable.truncated .truncatable-content { max-height: 200px; overflow: hidden; }
.truncatable.truncated::after { content: ''; position: absolute; bottom: 32px; left: 0; right: 0; height: 60px; background: linear-gradient(to bottom, transparent, var(--card-bg)); pointer-events: none; }
.message.user .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--user-bg)); }
.message.tool-reply .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--thinking-bg)); }
.tool-use .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--tool-bg)); }
.tool-result .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--tool-result-bg)); }
.expand-btn { display: none; width: 100%; padding: 8px 16px; margin-top: 4px; background: var(--control-bg); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 0.85rem; color: var(--text-muted); }
.expand-btn:hover { background: var(--control-bg-hover); }
.truncatable.truncated .expand-btn, .truncatable.expanded .expand-btn { display: block; }
.pagination { display: flex; justify-content: center; gap: 8px; margin: 24px 0; flex-wrap: wrap; }
.pagination a, .pagination span { padding: 5px 10px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; }
.pagination a { background: var(--card-bg); color: var(--user-border); border: 1px solid var(--user-border); }
.pagination a:hover { background: var(--user-bg); }
.pagination .current { background: var(--user-border); color: white; }
.pagination .disabled { color: var(--text-muted); border: 1px solid var(--border-subtle); }
.pagination .index-link { background: var(--user-border); color: white; }
details.continuation { margin-bottom: 16px; }
details.continuation summary { cursor: pointer; padding: 12px 16px; background: var(--user-bg); border-left: 4px solid var(--user-border); border-radius: 12px; font-weight: 500; color: var(--text-muted); }
details.continuation summary:hover { background: rgba(25, 118, 210, 0.15); }
details.continuation[open] summary { border-radius: 12px 12px 0 0; margin-bottom: 0; }
.index-item { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px var(--shadow-color); background: var(--user-bg); border-left: 4px solid var(--user-border); }
.index-item a { display: block; text-decoration: none; color: inherit; }
.index-item a:hover { background: rgba(25, 118, 210, 0.1); }
.index-item-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: var(--surface-bg); font-size: 0.85rem; }
.index-item-number { font-weight: 600; color: var(--user-border); }
.index-item-content { padding: 12px 16px; }
.index-item-stats { padding: 8px 16px 12px 16px; font-size: 0.8rem; color: var(--text-muted); border-top: 1px solid var(--border-subtle); }
.index-item-long-text { margin-top: 10px; background: var(--control-bg); border-radius: 8px; padding: 10px; border: 1px solid var(--border-subtle); }
.index-item-long-text-content { font-size: 0.85rem; }
.index-commit { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px var(--shadow-color); background: var(--card-bg); border-left: 4px solid var(--index-commit-border); }
.index-commit a { display: block; text-decoration: none; color: inherit; }
.index-commit a:hover { background: rgba(76, 175, 80, 0.08); }
.index-commit-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: var(--surface-bg); font-size: 0.85rem; }
.index-commit-hash { font-family: monospace; font-weight: 600; color: var(--write-header); }
.index-commit-msg { padding: 12px 16px; }
#search-box { display: flex; align-items: center; gap: 8px; }
#search-input, #modal-search-input { padding: 8px 12px; border: 1px solid var(--control-border); border-radius: 8px; font-size: 0.9rem; width: 200px; max-width: 60vw; background: var(--control-bg); color: var(--text-color); }
#search-btn, #modal-search-btn, #modal-close-btn { padding: 8px; border: 1px solid var(--control-border); border-radius: 8px; background: var(--control-bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; }
#search-btn:hover, #modal-search-btn:hover, #modal-close-btn:hover { background: var(--control-bg-hover); }
#search-modal { width: min(900px, 95vw); border: none; border-radius: 12px; padding: 0; box-shadow: 0 12px 40px rgba(0,0,0,0.25); background: var(--card-bg); color: var(--text-color); }
#search-modal::backdrop { background: var(--modal-backdrop); }
.search-modal-header { display: flex; gap: 8px; padding: 12px; border-bottom: 1px solid var(--border-subtle); align-items: center; }
#search-status { padding: 0 12px; color: var(--text-muted); font-size: 0.85rem; }
#search-results { padding: 12px; max-height: 70vh; overflow: auto; }
.search-result { padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; background: var(--control-bg); }
.search-result a { text-decoration: none; color: inherit; display: block; }
.search-result small { color: var(--text-muted); font-family: monospace; }
.search-highlight { background: rgba(255, 235, 59, 0.6); padding: 0 2px; border-radius: 3px; }

/* Shared controls */
.control-btn { padding: 8px; border: 1px solid var(--control-border); border-radius: 8px; background: var(--control-bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; line-height: 1; }
.control-btn:hover { background: var(--control-bg-hover); }
a.control-btn { text-decoration: none; }

/* Unknown record messages (format drift) */
.message.system { background: var(--system-bg); border-left: 4px solid var(--system-border); }
.message.system .role-label { color: var(--system-border); }
.system-record { background: color-mix(in srgb, var(--system-bg) 65%, var(--card-bg)); border: 1px solid color-mix(in srgb, var(--system-border) 25%, transparent); border-radius: 8px; padding: 12px; margin: 12px 0; }
.system-record-details summary { cursor: pointer; }
.system-record-badge { font-weight: 700; color: var(--system-border); text-transform: uppercase; letter-spacing: 0.6px; font-size: 0.72rem; }
.system-record-label { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--text-muted); font-size: 0.85rem; word-break: break-word; }

/* Viewer (index.html) */
.viewer-summary { margin: 0 0 12px 0; color: var(--text-muted); font-size: 0.9rem; }
.message.active { box-shadow: 0 0 0 2px color-mix(in srgb, var(--user-border) 65%, transparent), 0 1px 3px var(--shadow-color); }
.conversations { margin-top: 12px; }
.conversation-summary { cursor: pointer; padding: 0; list-style: none; }
.conversation-summary::-webkit-details-marker { display: none; }
.conversation-summary::marker { content: ""; }
.conversation-summary:hover .index-item-header { background: var(--hover-bg); }
.conversation[open] .index-item-header { border-bottom: 1px solid var(--border-subtle); }
.conversation-prompt { font-size: 1.55rem; font-weight: 500; line-height: 1.35; }
.conversation-prompt p { margin: 0; }
.conversation-prompt code { font-size: 0.85em; }
.conversation-stats-line { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.conversation .index-item-stats { font-size: 0.95rem; }
.conversation-body { padding: 14px 16px 2px 16px; }
.conversation-loading { padding: 12px 16px; color: var(--text-muted); font-size: 0.9rem; }
.conversation.filtered-out { display: none; }

.minimap-wrap { position: relative; background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: 12px; box-shadow: 0 1px 3px var(--shadow-color); padding: 6px 10px; margin: 0 0 12px 0; }
.minimap-wrap.minimap-large { padding: 10px 12px; }
#minimap { width: 100%; height: 64px; display: block; cursor: crosshair; }
.minimap-brush { position: absolute; top: 10px; left: 12px; right: 12px; bottom: 10px; pointer-events: none; }
.minimap-selection { position: absolute; top: 0; bottom: 0; border: 1px solid var(--control-border); border-radius: 10px; background: rgba(0,0,0,0.06); cursor: grab; pointer-events: auto; }
.minimap-selection.active { box-shadow: 0 0 0 9999px rgba(0,0,0,0.10); }
.minimap-handle { position: absolute; top: -2px; bottom: -2px; width: 12px; margin-left: -6px; background: var(--control-bg); border: 1px solid var(--control-border); border-radius: 10px; box-shadow: 0 1px 3px var(--shadow-color); cursor: ew-resize; pointer-events: auto; }
.minimap-handle::after { content: ''; position: absolute; left: 50%; top: 4px; bottom: 4px; width: 2px; transform: translateX(-1px); border-radius: 1px; background: var(--text-muted); opacity: 0.7; }
.minimap-tooltip { position: absolute; top: 6px; transform: translate(-50%, calc(-100% - 10px)); background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 8px 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.20); max-width: min(520px, 92vw); font-size: 0.85rem; color: var(--text-color); display: none; pointer-events: none; z-index: 10; }
.minimap-tooltip::after { content: ''; position: absolute; left: 50%; bottom: -8px; transform: translateX(-50%); border-width: 8px 8px 0 8px; border-style: solid; border-color: var(--border) transparent transparent transparent; }
.minimap-tip-title { font-weight: 600; margin-bottom: 4px; }
.minimap-tip-body { color: var(--text-muted); }
.minimap-tip-k { font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; font-size: 0.75rem; color: var(--text-muted); }
.minimap-tip-prompt { margin-top: 4px; color: var(--text-color); }

/* Help dialog */
.kb-help { width: min(720px, 95vw); border: none; border-radius: 12px; padding: 0; box-shadow: 0 12px 40px rgba(0,0,0,0.25); background: var(--card-bg); color: var(--text-color); }
.kb-help::backdrop { background: var(--modal-backdrop); }
.kb-help-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border-subtle); }
.kb-help-title { font-weight: 600; }
.kb-help-body { padding: 12px 14px; }
.kb-help-hint { color: var(--text-muted); font-size: 0.85rem; margin-bottom: 10px; }
.kb-help-pre { margin: 0; }
""" 


JS = """
(function() {
  function getSystemTheme() {
    try {
      return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    } catch (e) {
      return 'light';
    }
  }

  function getStoredTheme() {
    try {
      var t = localStorage.getItem('theme');
      return (t === 'light' || t === 'dark') ? t : null;
    } catch (e) {
      return null;
    }
  }

  function setStoredTheme(theme) {
    try {
      if (theme === 'light' || theme === 'dark') localStorage.setItem('theme', theme);
      else localStorage.removeItem('theme');
    } catch (e) {}
  }

  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }

  function updateThemeToggleLabel(btn) {
    var stored = getStoredTheme();
    var effective = stored || getSystemTheme();
    var modeLabel = stored ? stored : ('system (' + effective + ')');
    var hint = stored ? ' (click to toggle, shift-click for system)' : ' (click to toggle)';
    var title = 'Theme: ' + modeLabel + hint;
    btn.title = title;
    btn.setAttribute('aria-label', title);
  }

  function setupThemeToggle() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;

    updateThemeToggleLabel(btn);

    btn.addEventListener('click', function(e) {
      e.preventDefault();
      if (e.shiftKey) {
        setStoredTheme(null);
        applyTheme(null);
        updateThemeToggleLabel(btn);
        return;
      }
      var stored = getStoredTheme();
      var effective = stored || getSystemTheme();
      var next = effective === 'dark' ? 'light' : 'dark';
      setStoredTheme(next);
      applyTheme(next);
      updateThemeToggleLabel(btn);
    });

    try {
      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-color-scheme: dark)');
        var handler = function() {
          if (!getStoredTheme()) updateThemeToggleLabel(btn);
        };
        if (mq.addEventListener) mq.addEventListener('change', handler);
        else if (mq.addListener) mq.addListener(handler);
      }
    } catch (e) {}
  }

  function formatTimestamp(ts) {
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleString();
    } catch (e) {
      return ts;
    }
  }

  function updateTruncatables(root) {
    var scope = root || document;
    scope.querySelectorAll('.truncatable').forEach(function(el) {
      var content = el.querySelector('.truncatable-content');
      if (!content) return;
      var needs = content.scrollHeight > 240;
      if (needs && !el.classList.contains('expanded')) {
        el.classList.add('truncated');
      }
      var btn = el.querySelector('.expand-btn');
      if (!btn) return;
      btn.onclick = function() {
        el.classList.toggle('expanded');
        el.classList.toggle('truncated');
        btn.textContent = el.classList.contains('expanded') ? 'Show less' : 'Show more';
      };
    });
  }

  function enhance(root) {
    var scope = root || document;
    scope.querySelectorAll('time[data-timestamp]').forEach(function(t) {
      t.textContent = formatTimestamp(t.getAttribute('data-timestamp'));
    });
    updateTruncatables(scope);
  }

  // Expose for dynamically-inserted content (e.g. lazy-loaded conversation groups).
  window.__codexTranscriptsEnhance = enhance;

  enhance(document);
  setupThemeToggle();
})();
"""
