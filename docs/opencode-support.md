# opencode support (parked)

Opencode does not fit the current agent-discovery contract (path-list +
newest `.jsonl` wins) and is intentionally not wired up. Notes here so a
future pass has a starting point.

## What opencode writes

Observed on macOS, `~/.local/share/opencode/`:

- `opencode.db` — SQLite. Sessions and messages live here. This is the
  authoritative store.
- `storage/session_diff/ses_*.json` — one JSON object per session (not
  JSONL). Appears to hold file diffs, not the full transcript.
- `log/` — rotating logs, not conversation content.

No per-project directories, no JSONL transcripts. `~/.local/state/opencode/`
has `prompt-history.jsonl` but that is a global prompt history, not a
per-repo session transcript.

## Why it's parked

Adding opencode means either:

1. A SQLite reader that queries `opencode.db` for the newest session
   touching a given repo path, then synthesizes a transcript string.
2. A JSON reader for `storage/session_diff/*.json` that fuses diffs +
   messages into transcript form.

Either path introduces a driver/parser per agent — which the current
design explicitly rejects (`KNOWN_AGENT_DIRS` is a flat list of strings;
`extract_last_context` is one JSONL parser shared by all agents).

## When to unpark

Do it when the "one JSONL parser" assumption is already broken for
another reason — e.g. if we add a second SQLite-based agent, or if we
grow a real transcript abstraction. Until then, the cost (an
opencode-specific driver) outweighs the benefit (one more agent).

## Starting points if picking this up

- Inspect schema: `sqlite3 ~/.local/share/opencode/opencode.db .schema`
- Look for a session table with a repo/cwd column and a messages table
  keyed by session id.
- Reader signature would be
  `_read_opencode_session(repo_path: str) -> tuple[float, str] | None`
  returning (mtime, transcript_text) so it can compete with JSONL
  candidates in `find_agent_session`.
