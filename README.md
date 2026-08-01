# phasectl

A cognitive-mode manager for engineering work. Every session runs under a
named phase (`orient`, `ideate`, `design`, `impl`, `validate`, `snapshot`)
with its own system prompt, temperature, and token budget. Turns are stored
in SQLite; closed sessions are summarised and linked in a local graph.
Symbol lookup is delegated to an MCP-speaking code indexer over stdio.

phasectl is a Unix citizen: two binaries on `PATH` (`phasectl` and, if you
want symbol tools, `jcodemunch-mcp`) talking to each other over a pipe. No
daemons, no server, no domain knowledge baked into the phases. What is
"design" or "impl" is defined by a plain-text TOML file you can override.

## Install

```sh
uv tool install phasectl
uv tool install jcodemunch-mcp   # optional; needed for phasectl index/query and impl/validate tools
phasectl auth set                # or export ANTHROPIC_API_KEY
```

## Quick start

```sh
phasectl auth set
phasectl index --project myproject --path .
phasectl check --project myproject

phasectl start  --project myproject --phase orient
phasectl chat   "where are we?"
phasectl switch --phase impl
phasectl chat   "show me the main entry point"
phasectl close

phasectl loose  --project myproject
```

`phasectl check` reports pass/fail for every subsystem before you spend a
turn on the API. `phasectl loose` runs standalone and prints every
uncommitted, unmerged, or unpushed thread in the repo.

## Commands

| command               | what it does                                                                 |
|-----------------------|------------------------------------------------------------------------------|
| `phasectl start`      | Open a new session; in `orient` fuses git state + last session + last Claude Code transcript into a `Resume here:` block. |
| `phasectl chat MSG`   | Send a message under the current phase's prompt. Compresses old turns when the budget is exceeded. In `impl`/`validate` the model can call code-lookup tools. |
| `phasectl switch`     | Change the active session's phase. Prior turns remain visible.               |
| `phasectl close`      | Summarise the session using the `snapshot` prompt; link any `RFC-<n>` / `DECISION-<n>` tokens into the graph. |
| `phasectl status`     | Print the active session's phase, tokens, and turn count. `--json`.          |
| `phasectl index`      | Register a project's root path and hand it to `jcodemunch-mcp` for indexing. Path is remembered; later runs can omit `--path`. |
| `phasectl query SYM`  | Search the indexed symbols of a project. Standalone. `--json`.               |
| `phasectl graph`      | List closed sessions with linked RFCs and DECISIONs. `--json`.               |
| `phasectl loose`      | List every loose thread in a git repo — uncommitted files, unmerged branches, stashes, unpushed commits, extra worktrees. `--path`, `--base`, `--json`, `--synthesize`. |
| `phasectl check`      | Smoke-test every subsystem: config, auth, db, phases, index, mcp, git. No LLM calls. `--json`. |
| `phasectl auth set`   | Prompt for an API key and store it at `~/.config/phasectl/credentials` (chmod 600). |
| `phasectl auth status`| Report which source phasectl would load the key from.                        |

Run `phasectl COMMAND --help` for full flag documentation.

## Phases

Each phase is a TOML file with `name`, `temperature`, `token_budget`,
`tools_allowed`, and a `system_prompt`. The bundled defaults are behavioural
— they describe how to think, not what to think about — and can be
overridden per-user by dropping a file with the same name at
`~/.config/phasectl/phases/<name>.toml`.

| phase      | temp | budget | posture                                                                             |
|------------|-----:|-------:|-------------------------------------------------------------------------------------|
| `orient`   | 0.3  |  8 000 | Context handoff. Synthesise, don't plan. Ask when context is thin.                  |
| `ideate`   | 0.8  | 20 000 | Open exploration. Generate options and trade-offs, don't resolve them.              |
| `design`   | 0.3  | 12 000 | Structured decision-making with explicit rationale and rejected alternatives.       |
| `impl`     | 0.1  | 10 000 | Implement to a locked design. Can call code-search tools. Stops on ambiguity.       |
| `validate` | 0.1  | 10 000 | Write tests for specified behaviour; surface design/impl gaps. Can call code tools. |
| `snapshot` | 0.3  |  5 000 | Compress the session into a thirty-second handoff.                                  |

## Configuration

phasectl stores everything under `$XDG_CONFIG_HOME/phasectl` (default
`~/.config/phasectl`):

```
~/.config/phasectl/
├── config.toml           # global settings (see below)
├── credentials           # API key, chmod 600, plain text
├── sessions.db           # SQLite: sessions and turns
├── graph.json            # NetworkX MultiDiGraph on disk (closed sessions ↔ RFCs, DECISIONs)
└── phases/               # optional per-user phase overrides
    └── impl.toml
```

`config.toml` is created on first run with these sections:

```toml
[api]
model              = "claude-sonnet-4-6"          # for phasectl chat
compression_model  = "claude-haiku-4-5-20251001"  # for close / compress_tail / seed / loose --synthesize

[storage]
db_path = ""            # empty = use ~/.config/phasectl/sessions.db

[graph]
path = ""               # empty = use ~/.config/phasectl/graph.json

[defaults]
project = "myproject"   # used when a command omits --project

[claude_code]
projects_dir = ""       # override the auto-detected ~/.claude/projects lookup

[projects.myproject]
index_path = "/abs/path/to/myproject"   # written by 'phasectl index'
```

`ANTHROPIC_API_KEY` in the environment wins over `credentials`.
`XDG_CONFIG_HOME` moves the whole config directory.

## MCP tools

Code-aware operations are delegated to an external process that speaks the
Model Context Protocol over stdio. phasectl spawns it as
`jcodemunch-mcp serve` and calls a single `order` tool with an action name
(`search_symbols`, `get_symbol_source`, `get_file_outline`,
`get_ranked_context`, `get_blast_radius`, `get_untested_symbols`,
`index_folder`, `get_call_hierarchy`) plus arguments.

Swapping in a different indexer means editing the command in
`src/phasectl/tools.py::ToolExecutor._get_client`. Anything on `PATH` that
speaks MCP and exposes the same action names will work; there is no plugin
registry.

Impl and validate phases expose a subset of the same actions to Claude as
Anthropic tools (see `PHASE_TOOLS` in `tools.py`). The chat loop drives
tool_use rounds automatically until the model produces a plain-text turn.

## Design philosophy

- **Cognitive mode management, not an agent framework.** phasectl doesn't
  chain, plan, or route. It gates one API call at a time under a phase
  that shapes the model's posture.
- **No domain knowledge baked in.** The bundled phase prompts describe
  behaviour (how to think in this mode), not project specifics. Anything
  domain-shaped belongs in a per-user override.
- **Unix citizen.** Everything writes to `~/.config/phasectl`, everything
  is inspectable text (TOML, SQLite, JSON), and every command is
  pipeable. `--json` on the read commands is not decoration.
- **Two binaries over a pipe.** `phasectl` for state and prompts,
  `jcodemunch-mcp` for symbols. Each usable on its own; either replaceable
  without touching the other.

## Exit codes

- `0`  ok
- `1`  user error: bad flag, missing path, no active session-to-close conflict, no API key, failed check
- `2`  API failure: compression / chat error, invalid API key
- `3`  no active session (`chat`, `switch`, `close`)
- `4`  index unavailable (`query`)

## See also

- `docs/phasectl.1` — full man page (`man ./docs/phasectl.1`)
- `phasectl --help`, `phasectl COMMAND --help`
- `ARCHITECTURE.md`, `DESIGN.md`, `PHASES.md`
