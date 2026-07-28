# phasectl — personal Claude dev orchestration

## Intent

`phasectl` is a lightweight CLI tool that wraps the Anthropic API with phase-gated context
management for focused, disciplined engineering sessions on a single project. It runs
in the terminal, stores everything locally in SQLite, and enforces phase boundaries
so that Claude behaves differently depending on whether you are ideating, designing,
implementing, validating, or snapshotting.

The tool is built for one engineer working on **ContextOS** — a governed cognitive
memory system with 307+ tests, RFC → SPEC → TEST → Code discipline, DECISION-019
logged, and RFC-026 scoping AgentNode identity work.

## Constraints (non-negotiable)

- **Python 3.11+** — no version shenanigans
- **`anthropic` SDK only** — no LangChain, no agent frameworks, no orchestration libs
- **`sqlite3` stdlib** for session storage — no ORM, no SQLAlchemy
- **`typer`** for CLI
- **TOML** for phase config files
- **`kuzu` (embedded graph) is M2 only** — M1 ships `SQLiteStore` only. The
  `AbstractStore` Protocol exists so that `KuzuStore` can be swapped in without
  touching any consumer code.
- **All data local** under `~/.ctx/`
- **Compression always uses `claude-haiku-3-5`** — never the main model
- **Zero Docker, zero daemons, zero servers**
- **No placeholder functions, no TODO stubs** — every called function is implemented.
  If something is deferred to M2, it is not imported or called in M1.

## Core workflow

1. `phasectl start --project <name> --phase <phase>` — open a session
2. `phasectl chat "message"` — send a turn to Claude
3. `phasectl switch --phase <phase>` — change phase mid-session
4. `phasectl close` — compress session, persist summary, store to SQLite
5. `phasectl status` — show current session state

## Token budget & compression

Each phase has a `token_budget` (estimated tokens before compression fires). On every
turn, the sum of all non-compressed turn token_estimates in the current session is
checked. If it exceeds budget, the tail is compressed:

- Call `claude-haiku-3-5` with a compression prompt
- Replace the compressed turns with a `[COMPRESSED SUMMARY: ...]` block
- Mark those turns as `compressed = 1` in SQLite

Token estimation: `len(content) // 4`. Good enough for M1. No tokenizer dependency.

## Context injection

On `phasectl start` with a prior session, load `final_summary` + the last 3 raw
uncompressed turns, prepend as a system message to provide continuity.

## The six phases

| phase     | temp | budget | role |
|-----------|------|--------|------|
| orient    | 0.3  | 8 000  | Re-establish context, what's next |
| ideate    | 0.8  | 20 000 | Open exploration, question assumptions |
| design    | 0.3  | 12 000 | RFC/SPEC discipline, decisions |
| impl      | 0.1  | 10 000 | Implement to locked spec only |
| validate  | 0.1  | 10 000 | Test code only, no production code |
| snapshot  | 0.3  | 5 000  | Compress session to 30s orient summary |

## Out of scope (M1)

- Streaming responses
- Multi-project switching (one session at a time)
- Interactive REPL / readline editing
- Plugins or custom tool definitions
- Tokenizer-aware estimation (e.g., `tiktoken`)
- `KuzuStore` graph backend
- Any form of persistence beyond `~/.ctx/`

## Open Questions

1. **API key validation** — should `phasectl start` / `phasectl chat` fail fast if
   `ANTHROPIC_API_KEY` is unset, or should it defer to the SDK's own error? **Decision:
   fail fast in `api.py` init.**

2. **`phasectl status` with no active session** — should it show "no active session" or
   attempt to show the last closed session? **Decision: show "no active session" if
   none open, plus last closed session date if one exists.**

3. **Project override on `phasectl start`** — if a session is already open for project A
   and user runs `phasectl start` for project B, should it auto-close A or error?
   **Decision: error with "Session already active for project <A>. Close it first."**

4. **Turn limit for compression** — is compression triggered solely by token budget,
   or should there be a minimum turn count (e.g., compress only if ≥3 turns)?
   **Decision: compress only when turn count ≥ 3 AND over budget, to avoid
   compressing single-turn sessions into nothing.**

5. **`phasectl chat` without active session** — should it auto-create a session with
   defaults, or error? **Decision: error asking user to run `phasectl start` first.**
