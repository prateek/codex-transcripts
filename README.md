# codex-transcripts

Convert Codex session rollout files (`rollout-*.jsonl`) into a clean, mobile-friendly HTML transcript viewer (`index.html` + lazy-loaded chunks).

This is an adaptation of `simonw/claude-code-transcripts` (Apache-2.0) for Codex rollout files.
That project is the primary source of inspiration and the origin of the HTML/CSS transcript rendering approach used here.
See: https://github.com/simonw/claude-code-transcripts

Codex stores sessions under `~/.codex/` by default (override with `CODEX_HOME`):

- `~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl`
- `~/.codex/archived_sessions/rollout-...-<uuid>.jsonl` (archived via `thread/archive`)

## Install

```bash
# Run without cloning (one-off, via uvx)
uvx --from git+https://github.com/prateek/codex-transcripts codex-transcripts local --latest --open

# Or, from a local clone (persistent install)
git clone https://github.com/prateek/codex-transcripts
cd codex-transcripts
uv tool install .
```

## Usage

```bash
# Interactive picker for sessions (global by default)
codex-transcripts

# Convert a specific rollout file
codex-transcripts json ~/.codex/sessions/2026/01/01/rollout-...jsonl -o ./out --open

# Emit normalized JSON instead of HTML
codex-transcripts local --latest --format json -o ./out

# Publish to a GitHub Gist (requires gh auth)
codex-transcripts --gist

# TUI transcript viewer (experimental/alpha; fold/unfold + filtering)
codex-transcripts tui
```

HTML output is a single `index.html` view with fold/unfold, minimap + range filter, search, and keyboard shortcuts (`?`).

By default, `local` and `tui` search sessions globally; use `--cwd` to filter to the current working directory.

`local` supports multi-select; if you select more than one session it will generate an `index.html` archive in the output directory linking to each session’s viewer.

## System/internal records

Codex rollouts are JSONL. This tool is intentionally **best-effort**:

- All record types are included in the HTML output.
- Record types without a dedicated renderer are shown as *System* cards (raw JSON, with a count by type).
- Unparseable lines are skipped.

The outer envelope (`{"timestamp": "...", "type": "...", "payload": {...}}`) has been stable across multiple Codex versions; new `type` values and additional fields may appear over time.
