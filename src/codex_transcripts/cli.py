from __future__ import annotations

import json
from pathlib import Path

import click
from click_default_group import DefaultGroup
import questionary

from codex_transcripts.gist import create_gist
from codex_transcripts.rollout import (
    calculate_resume_style_metrics,
    format_resume_style_row,
    format_resume_style_header,
    format_updated_label,
    get_session_id_from_filename,
    list_session_rows,
)
from codex_transcripts.transcript import (
    as_meta_dict,
    default_output_dir,
    generate_html_from_rollout,
    generate_archive_index,
    generate_json_from_rollout,
    open_output,
    output_auto_dir,
)
from codex_transcripts.tui import run_tui


def _print_stats(stats) -> None:
    system = (
        stats.system_rollout_types
        or stats.system_event_types
        or stats.system_response_item_types
    )
    system_total = (
        sum(stats.system_rollout_types.values())
        + sum(stats.system_event_types.values())
        + sum(stats.system_response_item_types.values())
    )
    click.echo(
        f"Parsed: {stats.total_lines} lines, {stats.emitted_loglines} transcript items; "
        f"system: {system_total}"
    )
    if system:
        click.echo("System record types (rendered as System cards):", err=True)
        if stats.system_rollout_types:
            click.echo(f"- rollout: {stats.system_rollout_types}", err=True)
        if stats.system_event_types:
            click.echo(f"- event_msg: {stats.system_event_types}", err=True)
        if stats.system_response_item_types:
            click.echo(f"- response_item: {stats.system_response_item_types}", err=True)


def _ensure_output_dir(
    output: str | None,
    *,
    output_auto: bool,
    rollout_path: Path,
) -> tuple[Path, bool]:
    open_browser = False
    if output is None:
        out_dir = default_output_dir()
        open_browser = True
        return out_dir, open_browser

    parent = Path(output).expanduser()
    if output_auto:
        session_id = get_session_id_from_filename(rollout_path)
        out_dir = output_auto_dir(parent, session_id=session_id, filename=rollout_path.stem)
    else:
        out_dir = parent
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir, open_browser


@click.group(cls=DefaultGroup, default="local", default_if_no_args=True)
@click.version_option(None, "-v", "--version", package_name="codex-transcripts")
def cli() -> None:
    """Convert Codex rollout JSONL sessions into browsable HTML transcripts.

\b
Examples:
  codex-transcripts
  codex-transcripts local --latest --open
  codex-transcripts json ~/.codex/sessions/YYYY/MM/DD/rollout-...jsonl -o ./out --open
  codex-transcripts local --latest --gist
  codex-transcripts tui --latest  # experimental/alpha

\b
Run without cloning (from a git URL):
  uvx --from git+https://github.com/prateek/codex-transcripts codex-transcripts local --latest --open
    """


@cli.command("local")
@click.option("--codex-home", type=click.Path(path_type=Path), help="Override CODEX_HOME.")
@click.option("--limit", type=int, default=10, show_default=True, help="How many recent sessions to show.")
@click.option(
    "--cwd",
    "cwd_only",
    is_flag=True,
    help="Filter sessions to the current working directory (like `codex resume`).",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="(Deprecated) Search all sessions globally (default).",
)
@click.option("--include-archived/--no-include-archived", default=True, show_default=True)
@click.option("--query", help="Filter sessions by substring match (preview/cwd/branch/id/path).")
@click.option("--latest", is_flag=True, help="Use the most recent session (no interactive picker).")
@click.option("-o", "--output", type=click.Path(), help="Output directory (default: temp dir + open browser).")
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session id / filename (uses -o as parent).",
)
@click.option("--repo", help="GitHub repo owner/name for commit links (auto-detected from session meta if omitted).")
@click.option("--open", "open_browser", is_flag=True, help="Open generated index.html in your browser.")
@click.option("--gist", is_flag=True, help="Upload the generated HTML to a GitHub Gist (requires gh auth).")
@click.option("--gist-public", is_flag=True, help="Create a public Gist (default: secret).")
@click.option(
    "--include-source",
    "--json",
    "include_source",
    is_flag=True,
    help="Copy the source rollout file into the output directory.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["html", "json"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
def local_cmd(
    codex_home: Path | None,
    limit: int,
    cwd_only: bool,
    show_all: bool,
    include_archived: bool,
    query: str | None,
    latest: bool,
    output: str | None,
    output_auto: bool,
    repo: str | None,
    open_browser: bool,
    gist: bool,
    gist_public: bool,
    include_source: bool,
    output_format: str,
) -> None:
    if show_all and cwd_only:
        raise click.ClickException("--all and --cwd are mutually exclusive.")

    filter_cwd = Path.cwd() if cwd_only else None
    rows = list_session_rows(
        codex_home=codex_home,
        limit=limit,
        include_archived=include_archived,
        query=query,
        filter_cwd=filter_cwd,
    )
    if not rows:
        raise click.ClickException("No Codex sessions found under ~/.codex/sessions (or CODEX_HOME).")

    if output_format == "json" and (open_browser or gist):
        raise click.ClickException("--open/--gist are only supported for HTML output.")

    show_cwd = not cwd_only
    metrics = calculate_resume_style_metrics(rows, show_cwd=show_cwd)

    selected_paths: list[Path]
    if latest:
        selected_paths = [rows[0].path]
    else:
        click.echo(format_resume_style_header(metrics))
        choices = [
            questionary.Choice(
                title=format_resume_style_row(r, metrics=metrics),
                value=r.path,
            )
            for r in rows
        ]
        selected_paths = questionary.checkbox(
            "Select Codex sessions (space to toggle, enter to confirm):",
            choices=choices,
            validate=lambda a: True if a else "Select at least one session.",
        ).ask()
    if not selected_paths:
        raise click.ClickException("No session selected.")

    # For now, if multiple sessions are selected, write each into its own subdirectory under -o (or temp).
    # Single selection retains the original behavior.
    if len(selected_paths) == 1:
        selected = selected_paths[0]
        out_dir, open_by_default = _ensure_output_dir(output, output_auto=output_auto, rollout_path=selected)

        if output_format == "json":
            out_path, meta, stats = generate_json_from_rollout(
                selected,
                out_dir,
                include_source=include_source,
            )
            _print_stats(stats)
            if meta is not None:
                meta_path = out_dir / "session_meta.json"
                meta_path.write_text(
                    json.dumps(as_meta_dict(meta), indent=2, ensure_ascii=False) + "\n"
                )
            click.echo(f"JSON: {out_path}")
            click.echo(f"Output: {out_dir}")
            return

        out_html, meta, stats = generate_html_from_rollout(
            selected,
            out_dir,
            github_repo=repo,
            include_json=include_source,
        )

        _print_stats(stats)

        if meta is not None:
            meta_path = out_html.parent / "session_meta.json"
            meta_path.write_text(json.dumps(as_meta_dict(meta), indent=2, ensure_ascii=False) + "\n")

        if gist:
            click.echo("Creating GitHub gist...")
            gist_info = create_gist(out_html, public=gist_public)
            click.echo(f"Gist: {gist_info.gist_url}")
            if gist_info.preview_url:
                click.echo(f"Preview: {gist_info.preview_url}")
            if gist_info.raw_url:
                click.echo(f"Raw: {gist_info.raw_url}")

        if open_browser or open_by_default:
            open_output(out_html)

        click.echo(f"Output: {out_html}")
        return

    # Multi-selection: create output root and generate each session into an auto-named subdir.
    if gist:
        raise click.ClickException("Publishing multiple sessions to a single Gist is not supported yet. Re-run with a single selection or without --gist.")

    root, open_by_default = _ensure_output_dir(output, output_auto=False, rollout_path=selected_paths[0])
    rows_by_path = {r.path: r for r in rows}
    sessions_index: list[dict[str, str]] = []
    for p in selected_paths:
        subdir = output_auto_dir(root, session_id=get_session_id_from_filename(p), filename=p.stem)
        if output_format == "json":
            out_path, meta, stats = generate_json_from_rollout(
                p,
                subdir,
                include_source=include_source,
            )
        else:
            out_html, meta, stats = generate_html_from_rollout(
                p,
                subdir,
                github_repo=repo,
                include_json=include_source,
            )
            out_path = out_html
        _print_stats(stats)
        if meta is not None:
            (subdir / "session_meta.json").write_text(
                json.dumps(as_meta_dict(meta), indent=2, ensure_ascii=False) + "\n"
            )
        row = rows_by_path.get(p)
        sessions_index.append(
            {
                "session_id": (row.session_id if row else get_session_id_from_filename(p)) or subdir.name,
                "updated": format_updated_label(row) if row else "-",
                "updated_ts": 0 if row is None or row.updated_at is None else row.updated_at.timestamp(),
                "preview": (row.preview if row else p.name),
                "href": f"{subdir.name}/{out_path.name}",
            }
        )

    sessions_index.sort(key=lambda s: s.get("updated_ts", 0), reverse=True)
    if output_format == "html":
        generate_archive_index(root, sessions=sessions_index)
    else:
        (root / "index.json").write_text(
            json.dumps({"format": "codex-transcripts.index.v1", "sessions": sessions_index}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    if open_browser or open_by_default:
        if output_format == "html":
            open_output(root)

    click.echo(f"Output root: {root}")


@cli.command("tui")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--codex-home", type=click.Path(path_type=Path), help="Override CODEX_HOME (used when PATH is omitted).")
@click.option("--limit", type=int, default=50, show_default=True, help="How many recent sessions to show (when PATH is omitted).")
@click.option(
    "--cwd",
    "cwd_only",
    is_flag=True,
    help="Filter sessions to the current working directory (like `codex resume`).",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="(Deprecated) Search all sessions globally (default).",
)
@click.option("--include-archived/--no-include-archived", default=True, show_default=True)
@click.option("--query", help="Filter sessions by substring match (preview/cwd/branch/id/path) (when PATH is omitted).")
@click.option("--latest", is_flag=True, help="Use the most recent session (no interactive picker).")
def tui_cmd(
    path: Path | None,
    codex_home: Path | None,
    limit: int,
    cwd_only: bool,
    show_all: bool,
    include_archived: bool,
    query: str | None,
    latest: bool,
) -> None:
    """Interactive TUI transcript viewer (experimental/alpha; fold/unfold + filtering)."""
    rollout_path: Path
    if path is not None:
        rollout_path = path
    else:
        if show_all and cwd_only:
            raise click.ClickException("--all and --cwd are mutually exclusive.")

        filter_cwd = Path.cwd() if cwd_only else None
        rows = list_session_rows(
            codex_home=codex_home,
            limit=limit,
            include_archived=include_archived,
            query=query,
            filter_cwd=filter_cwd,
        )
        if not rows:
            raise click.ClickException("No Codex sessions found under ~/.codex/sessions (or CODEX_HOME).")

        show_cwd = not cwd_only
        metrics = calculate_resume_style_metrics(rows, show_cwd=show_cwd)

        if latest:
            rollout_path = rows[0].path
        else:
            click.echo(format_resume_style_header(metrics))
            choices = [
                questionary.Choice(
                    title=format_resume_style_row(r, metrics=metrics),
                    value=r.path,
                )
                for r in rows
            ]
            selected: Path | None = questionary.select(
                "Select a Codex session to view:", choices=choices, use_shortcuts=True
            ).ask()
            if selected is None:
                raise click.ClickException("No session selected.")
            rollout_path = selected

    if not rollout_path.exists():
        raise click.ClickException(f"File not found: {rollout_path}")

    run_tui(rollout_path=rollout_path)


@cli.command("json")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("-o", "--output", type=click.Path(), help="Output directory (default: temp dir + open browser).")
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session id / filename (uses -o as parent).",
)
@click.option("--repo", help="GitHub repo owner/name for commit links (auto-detected from session meta if omitted).")
@click.option("--open", "open_browser", is_flag=True, help="Open generated index.html in your browser.")
@click.option("--gist", is_flag=True, help="Upload the generated HTML to a GitHub Gist (requires gh auth).")
@click.option("--gist-public", is_flag=True, help="Create a public Gist (default: secret).")
@click.option(
    "--include-source",
    "--json",
    "include_source",
    is_flag=True,
    help="Copy the source rollout file into the output directory.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["html", "json"], case_sensitive=False),
    default="html",
    show_default=True,
    help="Output format.",
)
def json_cmd(
    path: Path,
    output: str | None,
    output_auto: bool,
    repo: str | None,
    open_browser: bool,
    gist: bool,
    gist_public: bool,
    include_source: bool,
    output_format: str,
) -> None:
    if not path.exists():
        raise click.ClickException(f"File not found: {path}")

    if output_format == "json" and (open_browser or gist):
        raise click.ClickException("--open/--gist are only supported for HTML output.")

    out_dir, open_by_default = _ensure_output_dir(output, output_auto=output_auto, rollout_path=path)
    if output_format == "json":
        out_path, meta, stats = generate_json_from_rollout(
            path,
            out_dir,
            include_source=include_source,
        )
        _print_stats(stats)
        if meta is not None:
            meta_path = out_dir / "session_meta.json"
            meta_path.write_text(json.dumps(as_meta_dict(meta), indent=2, ensure_ascii=False) + "\n")
        click.echo(f"JSON: {out_path}")
        click.echo(f"Output: {out_dir}")
        return

    out_html, meta, stats = generate_html_from_rollout(
        path,
        out_dir,
        github_repo=repo,
        include_json=include_source,
    )
    _print_stats(stats)

    if meta is not None:
        meta_path = out_html.parent / "session_meta.json"
        meta_path.write_text(json.dumps(as_meta_dict(meta), indent=2, ensure_ascii=False) + "\n")

    if gist:
        click.echo("Creating GitHub gist...")
        gist_info = create_gist(out_html, public=gist_public)
        click.echo(f"Gist: {gist_info.gist_url}")
        if gist_info.preview_url:
            click.echo(f"Preview: {gist_info.preview_url}")
        if gist_info.raw_url:
            click.echo(f"Raw: {gist_info.raw_url}")

    if open_browser or open_by_default:
        open_output(out_html)

    click.echo(f"Output: {out_html}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
