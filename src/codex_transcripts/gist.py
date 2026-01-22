from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click


@dataclass(frozen=True)
class GistInfo:
    gist_id: str
    gist_url: str
    raw_url: str | None
    preview_url: str | None
    owner_login: str | None
    latest_version: str | None


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


def _build_gist_info(*, gist_id: str, gist_url: str, html_filename: str) -> GistInfo:
    raw_url: str | None = None
    preview_url: str | None = None
    owner_login: str | None = None
    latest_version: str | None = None

    details = _fetch_gist_details(gist_id)
    if details:
        owner = details.get("owner", {})
        owner_login = owner.get("login") if isinstance(owner, dict) else None

        history = details.get("history", [])
        if isinstance(history, list) and history:
            h0 = history[0]
            if isinstance(h0, dict):
                latest_version = h0.get("version")

        files = details.get("files", {})
        filename: str | None = None
        if isinstance(files, dict) and files:
            if html_filename in files:
                filename = html_filename
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
        preview_url = f"https://gisthost.github.io/?{gist_id}/{html_filename}"

    return GistInfo(
        gist_id=gist_id,
        gist_url=gist_url,
        raw_url=raw_url,
        preview_url=preview_url,
        owner_login=owner_login if isinstance(owner_login, str) else None,
        latest_version=latest_version if isinstance(latest_version, str) else None,
    )


def get_gist_info(*, gist_id: str, gist_url: str, html_filename: str) -> GistInfo:
    return _build_gist_info(gist_id=gist_id, gist_url=gist_url, html_filename=html_filename)


def raw_gist_file_url(*, owner_login: str, gist_id: str, filename: str) -> str:
    return f"https://gist.githubusercontent.com/{owner_login}/{gist_id}/raw/{quote(filename)}"


def update_gist_file(*, gist_id: str, filename: str, content_file: str | Path) -> None:
    content_file = Path(content_file)
    if not content_file.exists():
        raise click.ClickException(f"File not found: {content_file}")
    _run_gh(
        [
            "gh",
            "api",
            "-X",
            "PATCH",
            f"/gists/{gist_id}",
            "-F",
            f"files[{filename}][content]=@{content_file}",
        ]
    )


def create_gist(
    html_file: str | Path,
    *,
    public: bool = False,
    extra_files: list[str | Path] | None = None,
    description: str | None = None,
) -> GistInfo:
    html_file = Path(html_file)
    if not html_file.exists():
        raise click.ClickException(f"HTML file not found: {html_file}")

    files: list[Path] = [html_file]
    if extra_files:
        files.extend(Path(p) for p in extra_files)
    for p in files:
        if not Path(p).exists():
            raise click.ClickException(f"File not found: {p}")

    cmd: list[str] = ["gh", "gist", "create", *(str(p) for p in files)]
    if public:
        cmd.append("--public")
    if description and description.strip():
        cmd.extend(["-d", description.strip()])
    result = _run_gh(cmd)

    gist_url = result.stdout.strip()
    gist_id = gist_url.rstrip("/").split("/")[-1]
    return _build_gist_info(gist_id=gist_id, gist_url=gist_url, html_filename=html_file.name)
