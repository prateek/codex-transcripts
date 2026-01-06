from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, Footer, Header, Input, Static, Tree

from codex_transcripts.rollout import RolloutParseError, parse_rollout_file


@dataclass(frozen=True)
class MessageUnit:
    timestamp: str
    kind: str
    title: str
    lines: list[str]
    search_text: str


@dataclass(frozen=True)
class ConversationGroup:
    prompt: MessageUnit | None
    units: list[MessageUnit]


def _pretty_json(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except TypeError:
        return str(value)


def _block_to_lines(block: dict[str, Any]) -> list[str]:
    btype = block.get("type")
    if btype == "text":
        text = block.get("text", "")
        return [text] if isinstance(text, str) else [str(text)]
    if btype == "thinking":
        text = block.get("thinking", "")
        return [text] if isinstance(text, str) else [str(text)]
    if btype == "tool_use":
        name = block.get("name", "tool")
        tool_input = block.get("input", {})
        header = f"$ {name}"
        return [header, _pretty_json(tool_input)]
    if btype == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)
        header = "tool_result (error)" if is_error else "tool_result"
        return [header, _pretty_json(content)]
    if btype == "system_record":
        label = block.get("label", "system_record")
        record = block.get("record", {})
        return [f"system_record: {label}", _pretty_json(record)]
    return [_pretty_json(block)]


def build_message_units(session_data: dict[str, Any]) -> list[MessageUnit]:
    units: list[MessageUnit] = []
    for entry in session_data.get("loglines", []):
        ts = entry.get("timestamp", "")
        etype = entry.get("type")
        message = entry.get("message", {})
        if not isinstance(message, dict):
            continue

        content = message.get("content", "")

        kind = "unknown"
        title = ""
        lines: list[str] = []

        if isinstance(content, str):
            if etype == "user":
                kind = "user"
                title = content.strip().splitlines()[0] if content.strip() else "(user)"
                lines = [content]
            elif etype == "system":
                kind = "system"
                title = content.strip().splitlines()[0] if content.strip() else "(system)"
                lines = [content]
            else:
                kind = "assistant"
                title = content.strip().splitlines()[0] if content.strip() else "(assistant)"
                lines = [content]
        elif isinstance(content, list):
            has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
            has_tool_result = any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            has_thinking = any(isinstance(b, dict) and b.get("type") == "thinking" for b in content)
            has_text = any(isinstance(b, dict) and b.get("type") == "text" for b in content)
            has_system_record = any(
                isinstance(b, dict) and b.get("type") == "system_record" for b in content
            )

            if etype == "system":
                kind = "system"
            elif has_tool_use:
                kind = "tool_call"
            elif has_tool_result:
                kind = "tool_result"
            elif has_thinking:
                kind = "thinking"
            elif etype == "user":
                kind = "user"
            elif etype == "assistant":
                kind = "assistant"

            for b in content:
                if isinstance(b, dict):
                    lines.extend(_block_to_lines(b))
                else:
                    lines.append(str(b))

            if etype == "system" and has_system_record:
                first = next(
                    (b for b in content if isinstance(b, dict) and b.get("type") == "system_record"),
                    None,
                )
                label = first.get("label") if isinstance(first, dict) else None
                title = f"system_record: {label or 'record'}"
            elif has_tool_use:
                first = next(
                    (b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"),
                    None,
                )
                tool_name = first.get("name") if isinstance(first, dict) else None
                if etype == "system":
                    title = f"system: {tool_name or 'tool'}"
                else:
                    title = f"tool_call: {tool_name or 'tool'}"
            elif has_tool_result:
                title = "tool_result"
            elif has_thinking:
                title = "thinking"
            elif has_text:
                first_text = next(
                    (b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)),
                    "",
                )
                title = first_text.strip().splitlines()[0] if first_text.strip() else kind
            else:
                title = kind
        else:
            lines = [str(content)]
            title = kind

        search_text = "\n".join(lines).lower()
        units.append(
            MessageUnit(
                timestamp=str(ts),
                kind=kind,
                title=title,
                lines=lines,
                search_text=search_text,
            )
        )

    return units


def filter_units(
    units: list[MessageUnit],
    *,
    query: str,
    show_user: bool,
    show_assistant: bool,
    show_tool_calls: bool,
    show_tool_results: bool,
    show_thinking: bool,
    show_system: bool,
) -> list[MessageUnit]:
    q = (query or "").strip().lower()
    allowed = set()
    if show_user:
        allowed.add("user")
    if show_assistant:
        allowed.add("assistant")
    if show_tool_calls:
        allowed.add("tool_call")
    if show_tool_results:
        allowed.add("tool_result")
    if show_thinking:
        allowed.add("thinking")
    if show_system:
        allowed.add("system")

    out: list[MessageUnit] = []
    for u in units:
        if u.kind not in allowed:
            continue
        if q and q not in u.search_text:
            continue
        out.append(u)
    return out


def group_units_by_prompt(units: list[MessageUnit]) -> list[ConversationGroup]:
    groups: list[ConversationGroup] = []
    current_prompt: MessageUnit | None = None
    current_units: list[MessageUnit] = []

    for u in units:
        if u.kind == "user":
            if current_prompt is not None or current_units:
                groups.append(ConversationGroup(prompt=current_prompt, units=current_units))
            current_prompt = u
            current_units = [u]
        else:
            current_units.append(u)

    if current_prompt is not None or current_units:
        groups.append(ConversationGroup(prompt=current_prompt, units=current_units))

    return groups


class TranscriptViewerApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("/", "focus_search", "Search"),
    ]

    def __init__(
        self,
        *,
        rollout_path: Path,
    ) -> None:
        super().__init__()
        self._rollout_path = rollout_path
        self._all_units: list[MessageUnit] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("TUI viewer status: ALPHA (experimental)", id="alpha-status")
            yield Static(f"Session: {self._rollout_path}", id="session-path")
            yield Input(placeholder="Filter by text…", id="query")
            with Horizontal():
                yield Checkbox("User", value=True, id="f-user")
                yield Checkbox("Assistant", value=True, id="f-assistant")
                yield Checkbox("Tool calls", value=True, id="f-tool-call")
                yield Checkbox("Tool results", value=True, id="f-tool-result")
                yield Checkbox("Thinking", value=False, id="f-thinking")
                yield Checkbox("System", value=True, id="f-system")
            yield Tree("Transcript", id="tree")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            session_data, _meta, _stats = parse_rollout_file(self._rollout_path)
        except RolloutParseError as e:
            self.exit(message=str(e))
            return
        self._all_units = build_message_units(session_data)
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        query = self.query_one("#query", Input).value
        show_user = self.query_one("#f-user", Checkbox).value
        show_assistant = self.query_one("#f-assistant", Checkbox).value
        show_tool_calls = self.query_one("#f-tool-call", Checkbox).value
        show_tool_results = self.query_one("#f-tool-result", Checkbox).value
        show_thinking = self.query_one("#f-thinking", Checkbox).value
        show_system = self.query_one("#f-system", Checkbox).value

        filtered = filter_units(
            self._all_units,
            query=query,
            show_user=show_user,
            show_assistant=show_assistant,
            show_tool_calls=show_tool_calls,
            show_tool_results=show_tool_results,
            show_thinking=show_thinking,
            show_system=show_system,
        )

        tree = self.query_one("#tree", Tree)
        tree.clear()
        root = tree.root
        root.label = f"Transcript ({len(filtered)}/{len(self._all_units)} shown)"

        visible = {id(u) for u in filtered}
        groups = group_units_by_prompt(self._all_units)
        rendered_groups = 0
        rendered_units = 0
        for group_idx, g in enumerate(groups, start=1):
            visible_units = [u for u in g.units if id(u) in visible]
            if not visible_units:
                continue

            rendered_groups += 1
            rendered_units += len(visible_units)

            if g.prompt is not None:
                group_label = f"{group_idx:04d}  {g.prompt.timestamp}  prompt  {g.prompt.title}"
            else:
                group_label = f"{group_idx:04d}  {visible_units[0].timestamp}  (no prompt yet)"

            group_node = root.add(group_label)
            for u in visible_units:
                header = f"{u.timestamp}  {u.kind}  {u.title}"
                node = group_node.add(header)
                for line in u.lines[:2000]:
                    for subline in str(line).splitlines() or [""]:
                        node.add_leaf(subline)

        root.label = f"Transcript ({rendered_units}/{len(self._all_units)} shown; {rendered_groups}/{len(groups)} prompts)"
        root.expand()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._refresh_tree()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self._refresh_tree()

    def action_focus_search(self) -> None:
        self.query_one("#query", Input).focus()


def run_tui(
    *,
    rollout_path: Path,
) -> None:
    app = TranscriptViewerApp(
        rollout_path=rollout_path,
    )
    app.run()
