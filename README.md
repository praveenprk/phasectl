# phasectl

**A cognitive-mode manager for engineering sessions.** Every LLM chat runs
under a named phase (`orient`, `ideate`, `design`, `impl`, `validate`,
`snapshot`) with its own system prompt, temperature, and token budget.

phasectl is provider-agnostic (Anthropic, OpenAI, Ollama, OpenRouter, or any
OpenAI-compatible endpoint) and indexer-agnostic (any MCP server on `PATH`).
Turns live in SQLite; closed sessions are summarised and linked in a local
graph. No daemons, no server, no vendor lock-in.

## Install

```sh
uv tool install phasectl
phasectl auth set                     # or export PHASECTL_API_KEY / ANTHROPIC_API_KEY
```

Using a local model via **Ollama**? Skip `phasectl auth set` — Ollama is
keyless. See [Swap providers](#swap-providers) for the two-line config change.

Optional — install a code indexer if you want `phasectl index/query` and the
`impl`/`validate` tool loop:

```sh
uv tool install jcodemunch-mcp        # or any MCP-speaking indexer on PATH
```

Any MCP server that exposes symbol-search tools works; the default indexer
binary is configured in `[tools].mcp_command` (see [Swap code indexers](#swap-code-indexers)).

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
turn on the API. `phasectl loose` runs standalone and lists every
uncommitted, unmerged, or unpushed thread in the repo.

## Commands

| command               | what it does                                                                 |
|-----------------------|------------------------------------------------------------------------------|
| `phasectl start`      | Open a new session; in `orient` fuses git state + last session + last coding-agent transcript (e.g. Claude Code) into a `Resume here:` block. |
| `phasectl chat MSG`   | Send a message under the current phase's prompt. Reads stdin as context when piped. Compresses old turns when the budget is exceeded. In `impl`/`validate` the model can call MCP tools. |
| `phasectl switch`     | Change the active session's phase. Prior turns remain visible.               |
| `phasectl close`      | Summarise the session using the `snapshot` prompt; link any `RFC-<n>` / `DECISION-<n>` tokens into the graph. |
| `phasectl status`     | Print the active session's phase, tokens, and turn count. `--json`.          |
| `phasectl index`      | Invoke `<indexer> index <path>` and register the path. Re-index by omitting `--path`. `--register-only` for external indexers. |
| `phasectl query SYM`  | Search the indexed symbols of a project. Standalone. `--json`.               |
| `phasectl graph`      | List closed sessions with linked RFCs and DECISIONs. `--json`.               |
| `phasectl loose`      | List every loose thread in a git repo — uncommitted files, unmerged branches, stashes, unpushed commits, extra worktrees. `--path`, `--base`, `--json`, `--synthesize`. |
| `phasectl check`      | Smoke-test every subsystem: config, auth, db, phases, index, mcp, git. No LLM calls. `--json`. |
| `phasectl auth set`   | Prompt for an API key and store it in the system keychain (macOS/Linux) or `~/.config/phasectl/credentials` (chmod 600). |
| `phasectl auth status`| Report which source phasectl would load the key from.                        |

Run `phasectl COMMAND --help` for full flag documentation. All operations
stream progress to stderr in git style (suppressed by `--quiet`); stdout stays
clean for piping.

## Swap providers

phasectl talks to any LLM backend via `[provider]` in `~/.config/phasectl/config.toml`
or the global `--backend` / `--model` / `--api-base` flags.

**Anthropic** (default):
```toml
[provider]
backend = "anthropic"
model = "claude-sonnet-4-6"
compression_model = "claude-haiku-4-5-20251001"
```

**OpenAI**:
```toml
[provider]
backend = "openai"
model = "gpt-4o"
compression_model = "gpt-4o-mini"
```

**Ollama** (local, keyless — no `phasectl auth set` needed):
```toml
[provider]
backend = "ollama"
model = "llama3.1:70b"
compression_model = "llama3.1:8b"
```

With `backend = "ollama"`, `phasectl auth status` reports
`not needed (backend=ollama)` and `phasectl check` treats the auth row as
passing without any key.

**OpenRouter** (many models behind one key):
```toml
[provider]
backend = "openrouter"
model = "anthropic/claude-sonnet-4-6"
compression_model = "openai/gpt-4o-mini"
```

Or override per-invocation:
```sh
phasectl --backend ollama --model llama3.1:70b chat "what is this?"
phasectl --model gpt-4o-mini chat "quick question"
```

## Swap code indexers

phasectl spawns whatever MCP server you name and uses the tools it discovers
via `tools/list` — no hardcoded schemas.

```toml
[tools]
mcp_command = "jcodemunch-mcp serve"   # default; change to swap indexers
```

Any MCP-speaking indexer with a similar tool surface works. Known-compatible:

- [jcodemunch-mcp](https://pypi.org/project/jcodemunch-mcp/) — the default,
  ships an `index` subcommand for `phasectl index`.
- Any MCP server that indexes externally: use
  `phasectl index --project <name> --path <dir> --register-only` to record the
  path without invoking the binary.

## Phases

Each phase is a TOML file with `name`, `temperature`, `token_budget`,
`tools_allowed`, and a `system_prompt`. The bundled defaults describe
*posture* (how to think in this mode), not project specifics. Override a
bundled phase — or add a new one — by dropping a file at
`~/.config/phasectl/phases/<name>.toml`. A file whose `[phase].name` is
not in the bundled set becomes a new phase (`phasectl start --phase <name>`).

| phase      | temp | budget | posture                                                                             |
|------------|-----:|-------:|-------------------------------------------------------------------------------------|
| `orient`   | 0.3  |  8 000 | Context handoff. Synthesise, don't plan. Ask when context is thin.                  |
| `ideate`   | 0.8  | 20 000 | Open exploration. Generate options and trade-offs, don't resolve them.              |
| `design`   | 0.3  | 12 000 | Structured decision-making with explicit rationale and rejected alternatives.       |
| `impl`     | 0.1  | 10 000 | Implement to a locked design. Can call code-search tools. Stops on ambiguity.       |
| `validate` | 0.1  | 10 000 | Write tests for specified behaviour; surface design/impl gaps. Can call code tools. |
| `snapshot` | 0.3  |  5 000 | Compress the session into a thirty-second handoff.                                  |

## Configuration

Everything lives under `$XDG_CONFIG_HOME/phasectl` (default `~/.config/phasectl`):

```
~/.config/phasectl/
├── config.toml           # global settings (see below)
├── credentials           # API key, chmod 600 (fallback if no keychain)
├── sessions.db           # SQLite: sessions and turns
├── graph.json            # NetworkX MultiDiGraph on disk (sessions ↔ RFCs, DECISIONs)
└── phases/               # optional per-user phase overrides
    └── impl.toml
```

`config.toml` is created on first run:

```toml
[provider]
backend = "anthropic"
model = ""                # empty = backend default (see Swap providers)
compression_model = ""
api_base = ""

[storage]
db_path = ""              # empty = ~/.config/phasectl/sessions.db

[graph]
path = ""                 # empty = ~/.config/phasectl/graph.json

# Optional. When a command omits --project, phasectl uses defaults.project
# if set here; otherwise it infers the project from the current directory
# (longest matching [projects.*].index_path, then the enclosing git repo's
# basename). Add this only if you want to pin one project globally.
# [defaults]
# project = "myproject"

[claude_code]
projects_dir = ""         # override auto-detected coding-agent transcript dir

[tools]
mcp_command = "jcodemunch-mcp serve"

[projects.myproject]
index_path = "/abs/path/to/myproject"   # written by 'phasectl index'
```

`PHASECTL_API_KEY` or `ANTHROPIC_API_KEY` in the environment wins over stored
credentials. `XDG_CONFIG_HOME` moves the whole config directory.

## Design philosophy

- **Cognitive mode management, not an agent framework.** phasectl doesn't
  chain, plan, or route. It gates one API call at a time under a phase that
  shapes the model's posture.
- **No domain knowledge baked in.** The bundled phase prompts describe
  *behaviour*, not project specifics. Anything domain-shaped belongs in a
  per-user override.
- **Unix citizen.** Everything writes to `~/.config/phasectl`, everything is
  inspectable text (TOML, SQLite, JSON), every command is pipeable, and
  `--json` on the read commands is not decoration.
- **Provider-agnostic.** No vendor lock-in in the code or the UX. Swap the
  backend or the indexer without touching phasectl.
- **Never writes to your codebase.** phasectl reads git state and indexed
  symbols; all storage (sessions, config, graph) lives under
  `~/.config/phasectl/`. Your working tree is never touched.

## Exit codes

- `0`  ok
- `1`  user error: bad flag, missing path, session-already-active, no API key, failed check
- `2`  API failure: compression or chat error, invalid API key
- `3`  no active session (`chat`, `switch`, `close`)
- `4`  index unavailable (`query`)

## See also

- `docs/phasectl.1` — full man page (`man ./docs/phasectl.1`)
- `phasectl --help`, `phasectl COMMAND --help`
- `CONTRIBUTING.md` — how to develop, add a phase, add a provider
- `docs/internal/` — architecture and design notes (development artifacts)

## License

MIT — see [LICENSE](LICENSE).
