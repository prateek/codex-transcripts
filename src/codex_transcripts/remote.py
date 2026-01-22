from __future__ import annotations

import errno
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx

from codex_transcripts.rollout import (
    CODEX_ARCHIVED_SESSIONS_SUBDIR,
    CODEX_SESSIONS_SUBDIR,
    ROLLOUT_FILENAME_RE,
    extract_session_meta_from_head,
    get_codex_home,
    read_rollout_head,
)


@dataclass(frozen=True)
class ImportedSession:
    url: str
    path: Path
    session_id: str | None
    timestamp: str | None


def _parse_rfc3339(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return str(uuid.UUID(s))
    except ValueError:
        return None


def _is_http_url(url: str) -> bool:
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        return False
    return scheme in {"http", "https"}


def _url_filename(url: str) -> str | None:
    try:
        p = urlparse(url)
    except ValueError:
        return None
    name = Path(p.path).name
    return name if name else None


def _download_url_to_tempfile(
    url: str,
    *,
    http_client: httpx.Client | None,
    timeout_s: float,
    max_bytes: int,
) -> Path:
    suffix = ".jsonl"
    name = _url_filename(url)
    if name:
        # Best-effort: preserve a useful suffix if present.
        lower = name.lower()
        if lower.endswith(".jsonl"):
            suffix = ".jsonl"
        elif lower.endswith(".json"):
            suffix = ".json"

    tmp = Path(tempfile.gettempdir()) / f"codex-transcripts-import-{uuid.uuid4()}{suffix}"

    def _write_with_client(client: httpx.Client) -> None:
        try:
            with client.stream("GET", url, timeout=timeout_s, follow_redirects=True) as resp:
                resp.raise_for_status()

                content_length = resp.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        n = int(content_length)
                    except ValueError:
                        n = None
                    if n is not None and n > max_bytes:
                        raise click.ClickException(
                            f"Remote file is too large ({n} bytes; max {max_bytes})."
                        )

                total = 0
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise click.ClickException(
                                f"Remote file exceeded max size ({max_bytes} bytes)."
                            )
                        f.write(chunk)
        except httpx.RequestError as e:
            raise click.ClickException(f"Failed to fetch URL: {e}") from e
        except httpx.HTTPStatusError as e:
            raise click.ClickException(
                f"Failed to fetch URL: {e.response.status_code} {e.response.reason_phrase}"
            ) from e

    try:
        if http_client is None:
            with httpx.Client() as client:
                _write_with_client(client)
        else:
            _write_with_client(http_client)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return tmp


def import_rollout_url(
    url: str,
    *,
    codex_home: str | Path | None = None,
    archived: bool = False,
    overwrite: bool = False,
    max_bytes: int = 50 * 1024 * 1024,
    timeout_s: float = 60.0,
    http_client: httpx.Client | None = None,
) -> ImportedSession:
    if not _is_http_url(url):
        raise click.ClickException("URL must start with http:// or https://")

    tmp = _download_url_to_tempfile(
        url,
        http_client=http_client,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
    )

    try:
        try:
            head = read_rollout_head(tmp)
        except UnicodeDecodeError as e:
            raise click.ClickException("Downloaded file is not valid UTF-8 JSONL.") from e

        meta = extract_session_meta_from_head(head)
        if meta is None:
            raise click.ClickException(
                "Downloaded file does not look like a Codex rollout (missing session_meta)."
            )

        dt = _parse_rfc3339(meta.timestamp) or _parse_rfc3339(head[0].get("timestamp") if head else None)
        if dt is None:
            dt = datetime.now(timezone.utc)

        session_id = _normalize_uuid(meta.id) or str(uuid.uuid4())
        ts_for_name = dt.strftime("%Y-%m-%dT%H-%M-%S")

        url_name = _url_filename(url)
        if url_name and ROLLOUT_FILENAME_RE.match(url_name):
            filename = url_name
        else:
            filename = f"rollout-{ts_for_name}-{session_id}.jsonl"

        home = get_codex_home(codex_home)
        if archived:
            dest_dir = home / CODEX_ARCHIVED_SESSIONS_SUBDIR
        else:
            dest_dir = (
                home
                / CODEX_SESSIONS_SUBDIR
                / f"{dt.year:04d}"
                / f"{dt.month:02d}"
                / f"{dt.day:02d}"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename

        if dest.exists() and not overwrite:
            raise click.ClickException(f"Session already exists: {dest} (use --overwrite)")

        try:
            tmp.replace(dest)
        except OSError as e:
            # Handle cross-device moves (EXDEV) by copying.
            if e.errno != errno.EXDEV:
                raise
            dest.write_bytes(tmp.read_bytes())
            tmp.unlink(missing_ok=True)

        return ImportedSession(url=url, path=dest, session_id=session_id, timestamp=meta.timestamp)
    finally:
        tmp.unlink(missing_ok=True)

