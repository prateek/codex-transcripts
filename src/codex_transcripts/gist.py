from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click


@dataclass(frozen=True)
class GistInfo:
    gist_id: str
    gist_url: str
    raw_url: str | None
    preview_url: str | None


def _run_gh(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to run gh: {error_msg}") from e
    except FileNotFoundError as e:
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        ) from e


def _fetch_gist_details(gist_id: str) -> dict[str, Any] | None:
    try:
        result = _run_gh(["gh", "api", f"/gists/{gist_id}"])
    except click.ClickException:
        return None
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def create_gist(html_file: str | Path, *, public: bool = False) -> GistInfo:
    html_file = Path(html_file)
    if not html_file.exists():
        raise click.ClickException(f"HTML file not found: {html_file}")

    cmd: list[str] = ["gh", "gist", "create", str(html_file)]
    if public:
        cmd.append("--public")
    result = _run_gh(cmd)

    gist_url = result.stdout.strip()
    gist_id = gist_url.rstrip("/").split("/")[-1]

    raw_url: str | None = None
    preview_url: str | None = None

    details = _fetch_gist_details(gist_id)
    if details:
        owner = details.get("owner", {})
        owner_login = owner.get("login") if isinstance(owner, dict) else None

        history = details.get("history", [])
        latest_version: str | None = None
        if isinstance(history, list) and history:
            h0 = history[0]
            if isinstance(h0, dict):
                latest_version = h0.get("version")

        files = details.get("files", {})
        filename: str | None = None
        if isinstance(files, dict) and files:
            if html_file.name in files:
                filename = html_file.name
            else:
                filename = next(iter(files.keys()))

        if filename and isinstance(files.get(filename), dict):
            raw = files[filename].get("raw_url")
            if isinstance(raw, str) and raw:
                raw_url = raw

        if (
            isinstance(owner_login, str)
            and owner_login
            and isinstance(latest_version, str)
            and latest_version
            and isinstance(filename, str)
            and filename
        ):
            # gistcdn.githack.com serves the file with a proper content-type so browsers render HTML.
            preview_url = (
                f"https://gistcdn.githack.com/{owner_login}/{gist_id}/raw/{latest_version}/{filename}"
            )

    if preview_url is None:
        # Best-effort fallback (works for single-file HTML gists, but depends on the preview host).
        preview_url = f"https://gisthost.github.io/?{gist_id}/{html_file.name}"

    return GistInfo(gist_id=gist_id, gist_url=gist_url, raw_url=raw_url, preview_url=preview_url)

