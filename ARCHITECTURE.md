# phasectl architecture

## File structure

```
phasectl/
├── pyproject.toml          # Pinned deps, entry point phasectl=phasectl.cli:app
├── DESIGN.md               # Intent, constraints, out-of-scope, open questions
├── PHASES.md               # Phase reference — all 6 phases documented
├── ARCHITECTURE.md         # This file
├── phases/                 # TOML phase definitions (read at runtime)
│   ├── orient.toml
│   ├── ideate.toml
│   ├── design.toml
│   ├── impl.toml
│   ├── validate.toml
│   └── snapshot.toml
└── src/
    └── phasectl/
        ├── __init__.py     # Package marker
        ├── cli.py          # typer app: start, chat, switch, close, status
        ├── api.py          # anthropic SDK wrapper: chat(), compress()
        ├── context.py      # estimate_tokens(), should_compress(), compress_tail(),
        │                   #   build_injection_block()
        ├── session.py      # Session dataclass, open_session(), close_session(),
        │                   #   load_prior_session()
        ├── phases.py       # Phase dataclass, load_phase(), list_phases()
        └── store.py        # AbstractStore Protocol + SQLiteStore implementation
```

## Data flow

```
User runs: phasectl start --project contextos --phase orient
   │
   ├─ cli.py: load config from XDG config (~/.config/phasectl/config.toml)
   ├─ cli.py: check for active session (error if exists)
   ├─ cli.py: load phase TOML (phases/orient.toml)
   ├─ cli.py: check for prior closed session (store.get_last_session)
   │   └─ if found: load final_summary + last 3 uncompressed turns
   │       └─ build injection block (context.build_injection_block)
   └─ cli.py: create new session (session.open_session → store.create_session)

User runs: phasectl chat "message"
   │
   ├─ cli.py: load config, load active session from store
   ├─ cli.py: load current phase config
   ├─ cli.py: get turns from store
   ├─ context.should_compress → if true:
   │   ├─ context.compress_tail
   │   │   ├─ pick compressible turns (all but last 2 uncompressed)
   │   │   ├─ call api.compress (haiku-3-5)
   │   │   ├─ store.mark_turns_compressed
   │   │   └─ store.add_turn (system role, [COMPRESSED SUMMARY: ...])
   │   └─ refresh turns
   ├─ cli.py: build messages array (system prompt + injection + turns)
   ├─ cli.py: store user turn
   ├─ api.chat (sonnet-4-6, temperature from phase)
   ├─ cli.py: store assistant turn
   └─ cli.py: print response

User runs: phasectl switch --phase impl
   ├─ load phase TOML, update in-memory current_phase
   └─ print phase identity

User runs: phasectl close
   ├─ load snapshot phase TOML
   ├─ format uncompressed turns as text
   ├─ api.compress (haiku-3-5, snapshot prompt)
   ├─ store.close_session (summary, total_tokens, closing_phase)
   └─ print summary

User runs: phasectl status
   ├─ check active session → show project, phase, session id, token count
   └─ if none → show last closed session date or "no session"
```

## Layer interface contracts

### store.py — AbstractStore Protocol

```python
class AbstractStore(Protocol):
    def create_session(self, id: str, project: str, phase: str, created_at: str) -> None: ...
    def get_session(self, id: str) -> dict | None: ...
    def update_session(self, id: str, **kwargs) -> None: ...
    def close_session(self, id: str, phase: str, summary: str, total_tokens: int) -> None: ...
    def add_turn(self, session_id: str, phase: str, role: str, content: str,
                 token_estimate: int) -> int: ...
    def get_turns(self, session_id: str) -> list[dict]: ...
    def get_active_session(self, project: str) -> dict | None: ...
    def get_last_session(self, project: str) -> dict | None: ...
    def get_uncompressed_turns(self, session_id: str) -> list[dict]: ...
    def mark_turns_compressed(self, turn_ids: list[int]) -> None: ...
    def get_last_uncompressed_turns(self, session_id: str, n: int) -> list[dict]: ...
```

### session.py — lifecycle functions

```python
Session(id, project, created_at, closed_at, opening_phase, closing_phase,
        final_summary, total_tokens)

open_session(store, project, phase) → Session
close_session(store, session_id, phase, summary, total_tokens) → None
load_prior_session(store, project) → (session_dict, last_turns) | None
```

### context.py — compression & injection

```python
estimate_tokens(content) → int                        # len(c)//4
should_compress(turns, token_budget) → bool            # ≥3 turns AND over budget
compress_tail(session_id, store, app_config) → str     # call haiku, store summary
build_injection_block(prior_session, prior_turns) → str # format for system prompt
```

### phases.py — TOML loader

```python
Phase(name, system_prompt, temperature, token_budget, tools_allowed)

load_phase(name) → Phase     # read from phases/<name>.toml
list_phases() → list[str]    # enumerate *.toml files in phases/
```

### api.py — Anthropic wrapper

```python
chat(messages, system_prompt, temperature, app_config) → str
    # calls claude-sonnet-4-6 (or configured model)
    # messages: list of {"role": "user"|"assistant", "content": str}
    # returns response text

compress(turns_text, app_config) → str
    # calls claude-haiku-3-5 (compression model)
    # returns summary text
```

## SQLite DDL (exact)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    closed_at       TEXT,
    opening_phase   TEXT NOT NULL,
    closing_phase   TEXT,
    final_summary   TEXT,
    total_tokens    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    phase           TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    token_estimate  INTEGER NOT NULL,
    timestamp       TEXT NOT NULL,
    compressed      INTEGER NOT NULL DEFAULT 0
);
```

## Config schema (XDG: ~/.config/phasectl/config.toml)

Created automatically on first run. Never stored: ANTHROPIC_API_KEY (env only).

```toml
[api]
model = "claude-sonnet-4-6"
compression_model = "claude-haiku-3-5"

[storage]
db_path = ""   # empty = XDG default (~/.config/phasectl/sessions.db)

[defaults]
project = "contextos"
window_tokens = 15000
keep_last_turns = 3
```

## Compression mechanics

1. `should_compress()` counts all uncompressed turns in the session.
   - If count ≥ 3 AND sum of token_estimates > phase.token_budget → trigger.
2. `compress_tail()` picks all uncompressed turns except the last 2.
3. Formats them as `[phase][role]: content` blocks.
4. Calls `api.compress()` which sends to `claude-haiku-3-5` with a compression
   system prompt.
5. Marks the compressed turns as `compressed = 1` in SQLite.
6. Inserts a new system turn with `[COMPRESSED SUMMARY: <haiku response>]`.
7. Future message builds filter out `compressed = 1` turns.

## Context injection mechanics

On `phasectl start` with a prior session:
1. `load_prior_session()` queries `get_last_session(project)`.
2. Gets `final_summary` from the session record.
3. Gets last 3 uncompressed turns via `get_last_uncompressed_turns(session_id, 3)`.
4. `build_injection_block()` formats these as:
   ```
   [Prior Session Context]
   Prior session summary:
   <final_summary>

   Last turns from prior session:
   [user]: ...
   [assistant]: ...
   ```
5. This block is prepended to the current phase's system prompt, separated by `---`.

## Phase lifecycle

```
phasectl start --phase orient
   │
   ├── orient ──phasectl switch──► ideate
   │                            │
   │                            ├── phasectl switch ──► design
   │                            │                    │
   │                            │                    ├── phasectl switch ──► impl
   │                            │                    │                    │
   │                            │                    │                    ├── phasectl switch ──► validate
   │                            │                    │                    │
   │                            │                    │                    └── phasectl close ──► snapshot (auto)
   │                            │                    │
   │                            │                    └── phasectl switch ──► impl ...
   │                            │
   │                            └── phasectl switch ──► orient (cycle back)
   │
   └── phasectl close ──► snapshot (auto, uses haiku to summarise all turns)
```

Phases are not a linear pipeline. The engineer switches freely. Each turn records
the phase it was taken in. `phasectl close` always uses the `snapshot` prompt to compress
the session into a final summary, regardless of the current phase.
