# INVARIANT: phasectl is read-only against the user's codebase.
# It reads git state, reads indexed symbols, reads transcripts.
# It writes ONLY to ~/.config/phasectl/ (config, sessions, graph).
# No command may create, modify, or delete files under the project path.
# This is a design constraint, not an implementation detail.

import sys
import json
import shlex
import subprocess
import typer
import tomllib
import os

from .store import SQLiteStore
from .session import open_session, close_session, load_prior_session
from .phases import load_phase, list_phases
from .context import estimate_tokens, should_compress, compress_tail, build_injection_block
from .api import (
    chat as api_chat,
    compress as api_compress,
    persist_session_graph,
    extract_rfcs_and_decisions,
)
from .config import (
    get_config_file,
    get_db_path,
    get_index_path,
    set_index_path,
    get_coding_agent_projects_dir,
)
from .tools import ToolExecutor
from .resume import (
    get_git_state,
    find_agent_session,
    extract_last_context,
    build_resume_block,
)
from .loose import (
    is_git_repo,
    collect as loose_collect,
    render_human as loose_render_human,
    resolve_repo as loose_resolve_repo,
    synthesize_top_line as loose_synthesize_top_line,
)
from .seed import build_seed_block
from .auth import AuthError, key_source, save_api_key, remove_api_key
from .progress import Progress


def _provider_label(config: dict) -> str:
    p = config.get("provider") or {}
    backend = (p.get("backend") or "anthropic").strip()
    model = (p.get("model") or "").strip() or "(default)"
    return f"{backend}/{model}"


def _compression_label(config: dict) -> str:
    p = config.get("provider") or {}
    backend = (p.get("backend") or "anthropic").strip()
    model = (p.get("compression_model") or p.get("model") or "").strip() or "(default)"
    return f"{backend}/{model}"


def _fmt_tool_call(name: str, arguments: dict) -> str:
    """Compact repr of a tool call for a progress line.

    Picks the first str-valued arg as a single positional-ish label so
    the line stays short: `search_symbols("AgentNode")`.
    """
    label = ""
    for v in (arguments or {}).values():
        if isinstance(v, str):
            label = v
            break
    if label:
        if len(label) > 40:
            label = label[:37] + "..."
        return f"{name}({label!r})"
    return f"{name}()"


_CLI_OVERRIDES: dict = {}


app = typer.Typer(
    help=(
        "phasectl — a cognitive-mode manager for engineering sessions.\n\n"
        "Each session runs under a named phase (orient, ideate, design, impl, "
        "validate, snapshot) with its own system prompt, temperature, and token "
        "budget. Turns are stored in SQLite; closed sessions are summarised and "
        "linked in a local graph. Symbol lookup is delegated to an MCP-speaking "
        "code indexer configured in tools.mcp_command in config.toml."
    ),
    epilog="Run 'phasectl COMMAND --help' for details on any command.",
    no_args_is_help=True,
)
auth_app = typer.Typer(
    help=(
        "Manage the LLM API key. Sources checked in order: $PHASECTL_API_KEY "
        "or $ANTHROPIC_API_KEY, then the system keychain (macOS Keychain / "
        "freedesktop secret-service), then ~/.config/phasectl/credentials."
    ),
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


def _version_callback(value: bool) -> None:
    if not value:
        return
    from . import __version__
    print(f"phasectl {__version__}")
    raise typer.Exit(0)


@app.callback()
def main(
    model: str = typer.Option(
        None, "--model", "-m",
        help="Override the chat model for this invocation. Wins over config.toml.",
    ),
    backend: str = typer.Option(
        None, "--backend", "-b",
        help="LLM backend: anthropic, openai, ollama, openrouter, custom. Wins over config.toml.",
    ),
    api_base: str = typer.Option(
        None, "--api-base",
        help="Override the API base URL for this invocation. Wins over config.toml.",
    ),
    version: bool = typer.Option(
        None, "--version", "-v", "-V",
        callback=_version_callback, is_eager=True,
        help="Show the phasectl version and exit.",
    ),
):
    """phasectl — cognitive-mode manager for engineering sessions."""
    if model is not None:
        _CLI_OVERRIDES["model"] = model
    if backend is not None:
        _CLI_OVERRIDES["backend"] = backend
    if api_base is not None:
        _CLI_OVERRIDES["api_base"] = api_base


def _diag(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def _die_on_auth_error(exc: AuthError) -> None:
    print(str(exc), file=sys.stderr)
    raise typer.Exit(exc.exit_code)


_DEFAULT_CONFIG_TEMPLATE = '''\
[provider]
# backend: anthropic | openai | ollama | openrouter | custom
# Ollama is keyless (runs locally); the others require an API key via
# `phasectl auth set`, $PHASECTL_API_KEY, or $ANTHROPIC_API_KEY.
backend = "anthropic"

# model names depend on your backend:
#   anthropic:  claude-sonnet-4-6, claude-haiku-4-5-20251001
#   openai:     gpt-4o, gpt-4o-mini
#   ollama:     llama3.1:70b, llama3.1:8b
#   openrouter: anthropic/claude-sonnet-4-6, openai/gpt-4o
# Leave empty to let the backend pick its own sensible default.
model = ""
compression_model = ""

# api_base: leave empty for provider defaults, or set for custom endpoints
#   anthropic:  https://api.anthropic.com (default)
#   openai:     https://api.openai.com/v1 (default)
#   ollama:     http://localhost:11434/v1 (default)
#   openrouter: https://openrouter.ai/api/v1 (default)
api_base = ""

[storage]
db_path = ""

[graph]
path = ""

[coding_agent]
# Override the auto-detected coding-agent transcript dir. When set, phasectl
# scans only this dir; otherwise it scans all known agent locations
# (~/.claude, ~/.codex, ~/.cursor, ~/.continue). Env var
# PHASECTL_AGENT_DIR overrides both.
projects_dir = ""

[tools]
# MCP-speaking indexer spawned for `phasectl index` and the impl/validate
# tool loop. Swap the command to swap indexers.
mcp_command = "jcodemunch-mcp serve"

# Per-project index paths are written here by `phasectl index`:
# [projects.myproject]
# index_path = "/abs/path/to/myproject"
'''


_KEYLESS_BACKENDS = {"ollama"}


def _backend_from_config(config: dict) -> str:
    return (config.get("provider", {}) or {}).get("backend", "") or ""


def _is_keyless(config: dict) -> bool:
    return _backend_from_config(config) in _KEYLESS_BACKENDS


def _git_toplevel(path: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if r.returncode == 0:
            top = r.stdout.strip()
            return top or None
    except Exception:
        pass
    return None


def _infer_project_from_cwd(config: dict) -> str | None:
    """Infer a project name from cwd: longest matching [projects.*].index_path
    wins; otherwise fall back to the git repo root's basename."""
    try:
        cwd = os.path.realpath(os.getcwd())
    except Exception:
        return None
    projects = config.get("projects", {}) or {}
    best: tuple[int, str] | None = None
    for name, meta in projects.items():
        if not isinstance(meta, dict):
            continue
        idx = meta.get("index_path", "") or ""
        if not idx:
            continue
        try:
            idx_abs = os.path.realpath(os.path.expanduser(idx))
        except Exception:
            continue
        try:
            common = os.path.commonpath([cwd, idx_abs])
        except ValueError:
            continue
        if common == idx_abs:
            n = len(idx_abs)
            if best is None or n > best[0]:
                best = (n, name)
    if best:
        return best[1]
    root = _git_toplevel(cwd)
    if root:
        return os.path.basename(root)
    return None


def _resolve_project(project: str | None, config: dict) -> str:
    """Return an explicit project name, or exit 1 if none can be determined.

    Order: --project → defaults.project → cwd inference (matching
    [projects.*].index_path or the enclosing git repo's basename).
    """
    if project:
        return project
    default = (config.get("defaults", {}) or {}).get("project", "") or ""
    if default:
        return default
    inferred = _infer_project_from_cwd(config)
    if inferred:
        return inferred
    print(
        "no project specified. Pass --project <name> or run from inside a project directory.",
        file=sys.stderr,
    )
    raise typer.Exit(1)


def _load_config() -> dict:
    config_file = get_config_file()
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(_DEFAULT_CONFIG_TEMPLATE)
    with open(config_file, "rb") as f:
        config = tomllib.load(f)

    # Auto-migrate the legacy [api] section into [provider] (in-memory only).
    if "provider" not in config and "api" in config:
        api = config["api"]
        config["provider"] = {
            "backend": "anthropic",
            "api_base": api.get("api_base", ""),
            "model": api.get("model", ""),
            "compression_model": api.get("compression_model", ""),
        }

    # CLI overrides win over the file. Applied last so this call reflects flags
    # passed to the outer `phasectl --backend X --model Y ...` callback.
    if _CLI_OVERRIDES:
        provider = config.setdefault("provider", {})

        # If --backend flips the backend, the model IDs and api_base baked
        # into the file target the OLD backend and would 404 against the
        # new one (e.g. compression_model="claude-haiku-…" on ollama).
        # Wipe them so provider defaults, or the CLI --model, kick in.
        stored_backend = (provider.get("backend") or "").strip()
        new_backend = str(_CLI_OVERRIDES.get("backend", stored_backend)).strip()
        backend_changed = (
            "backend" in _CLI_OVERRIDES and new_backend != stored_backend
        )
        if backend_changed:
            provider["model"] = ""
            provider["compression_model"] = ""
            if "api_base" not in _CLI_OVERRIDES:
                provider["api_base"] = ""

        for key in ("model", "backend", "api_base"):
            if key in _CLI_OVERRIDES:
                provider[key] = _CLI_OVERRIDES[key]

        # --model applies to ALL model calls: if compression_model isn't
        # set explicitly, mirror --model onto it so the compression path
        # doesn't fall back to a stale (or provider-mismatched) value.
        if (
            "model" in _CLI_OVERRIDES
            and not (provider.get("compression_model") or "").strip()
        ):
            provider["compression_model"] = _CLI_OVERRIDES["model"]

    return config


def _get_store(config: dict) -> SQLiteStore:
    raw = config["storage"]["db_path"]
    db = raw if raw else str(get_db_path())
    return SQLiteStore(db)


def _derive_current_phase(store: SQLiteStore, session_data: dict) -> str:
    if session_data.get("closing_phase"):
        return session_data["closing_phase"]
    turns = store.get_turns(session_data["id"])
    if turns:
        return turns[-1]["phase"]
    return session_data["opening_phase"]


@app.command()
def start(
    project: str = typer.Option(None, "--project", "-p", help="Project name; scopes sessions, index path, and graph. Defaults to defaults.project from config."),
    phase: str = typer.Option("orient", "--phase", help="Phase to open in (orient|ideate|design|impl|validate|snapshot; user overrides in ~/.config/phasectl/phases also accepted)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the diagnostic lines on stderr; the session id and any resume block still print on stdout."),
    no_fuse: bool = typer.Option(
        False, "--no-fuse", help="Skip the orient-phase fusion of git state + the last external coding-agent transcript. The last phasectl session summary is still injected.",
    ),
    seed: list[str] = typer.Option(
        None, "--seed", help="Path to a .md/.txt/.json file whose gist is compressed by the LLM and injected as system context. Repeat for multiple files.",
    ),
):
    """Open a new session for a project in the chosen phase.

    Fails with exit 1 if a session is already active for the project
    (close it first) or if the phase name is unknown.

    In the default 'orient' phase (unless --no-fuse), phasectl fuses three
    signals into a 'Resume here:' block on stdout: (1) git state of the
    indexed repo, (2) the closed summary of the last session, (3) the tail
    of the most recent external coding-agent transcript for that repo (if
    one is found). Any --seed files are compressed and added as extra
    system context.
    """
    progress = Progress(quiet=quiet)
    progress.step("loading config...")
    config = _load_config()
    project = _resolve_project(project, config)
    store = _get_store(config)

    active = store.get_active_session(project)
    if active:
        progress.done()
        print(
            f"Session already active for project '{project}'. Close it first.",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    try:
        load_phase(phase)
    except FileNotFoundError:
        progress.done()
        available = list_phases()
        print(
            f"Phase '{phase}' not found. "
            f"Available phases: {', '.join(available)}",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    prior = load_prior_session(store, project)
    if prior:
        progress.step(f"loading prior session ({prior[0]['id'][:8]})...")
    else:
        progress.step(f"no prior session for '{project}' — starting fresh...")

    resume_block = ""
    if phase == "orient" and not no_fuse:
        session_summary = ""
        if prior:
            session_summary = (prior[0].get("final_summary") or "").strip()

        repo_path_obj = get_index_path(project, config)
        repo_path = str(repo_path_obj) if repo_path_obj else ""
        git_state: dict = {}
        agent_context = ""
        if repo_path:
            try:
                git_state = get_git_state(repo_path)
                branch = git_state.get("branch") or "detached"
                dirty = git_state.get("dirty_count", 0)
                progress.step(f"reading git state ({branch}, {dirty} dirty)...")
            except Exception as e:
                _diag(f"[orient] git introspection skipped: {e}", quiet=quiet)
            try:
                agent_dir = get_coding_agent_projects_dir(config)
                agent_path = find_agent_session(repo_path, agent_dir)
                if agent_path:
                    progress.step("reading coding-agent transcript...")
                    agent_context = extract_last_context(agent_path)
            except Exception as e:
                _diag(f"[orient] coding-agent transcript lookup skipped: {e}", quiet=quiet)
        else:
            _diag(
                f"[orient] no indexed path for '{project}' — fusion limited to session summary.",
                quiet=quiet,
            )

        try:
            progress.step(
                f"synthesizing resume block via {_compression_label(config)}..."
            )
            resume_block = build_resume_block(git_state, session_summary, agent_context, config)
        except Exception as e:
            _diag(f"[orient] synthesis failed: {e}", quiet=quiet)
            resume_block = ""

    progress.step("opening session...")
    if prior:
        session_data, turns = prior
        summary = session_data.get("final_summary", "") or ""
        close_phase = session_data.get("closing_phase", "unknown") or "unknown"
        close_date = session_data.get("closed_at", "unknown")
        if close_date != "unknown":
            close_date = close_date[:16]

        injection = build_injection_block(session_data, turns)
        session = open_session(store, project, phase)
        store.add_turn(
            session_id=session.id,
            phase=phase,
            role="system",
            content=injection,
            token_estimate=estimate_tokens(injection),
        )
    else:
        session = open_session(store, project, phase)

    if resume_block:
        store.add_turn(
            session_id=session.id,
            phase=phase,
            role="system",
            content=resume_block,
            token_estimate=estimate_tokens(resume_block),
        )

    seed_paths = seed or []
    if seed_paths:
        progress.step(f"compressing {len(seed_paths)} seed file(s)...")
        seed_block = build_seed_block(
            seed_paths,
            config,
            diag=lambda msg: _diag(msg, quiet=quiet),
        )
        if seed_block:
            store.add_turn(
                session_id=session.id,
                phase=phase,
                role="system",
                content=seed_block,
                token_estimate=estimate_tokens(seed_block),
            )

    progress.done()
    if resume_block:
        print(resume_block)
    print(f"session opened: {session.id[:8]} {phase}")


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send. Wrap in quotes for anything longer than one word."),
    model: str = typer.Option(
        None, "--model", "-m",
        help="Override the chat model for this single message. Wins over config.toml and the global --model.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the 'Compressing tail turns…' diagnostic on stderr."),
):
    """Send a message in the active session and print the reply.

    Uses the current phase's system prompt, temperature, and token budget.
    If the running token estimate exceeds the phase budget, older turns are
    compressed into a summary before the request. In the impl and validate
    phases the model can call code-lookup tools discovered from the
    configured MCP server; the tool loop runs until the model produces a
    plain text reply.

    Exit codes: 3 if no session is active, 2 for other API errors,
    1 or 2 for auth errors (see 'phasectl auth --help').
    """
    if model is not None:
        _CLI_OVERRIDES["model"] = model
    progress = Progress(quiet=quiet)

    stdin_content = ""
    if not sys.stdin.isatty():
        stdin_raw = sys.stdin.read()
        original_len = len(stdin_raw)
        max_chars = 12000
        if original_len > max_chars:
            stdin_content = stdin_raw[:max_chars]
            print(
                f"stdin: {original_len} chars truncated to {max_chars}",
                file=sys.stderr,
            )
        else:
            stdin_content = stdin_raw
        if stdin_content:
            progress.step(f"reading stdin ({len(stdin_content)} chars)...")

    if stdin_content:
        full_message = f"<context>\n{stdin_content}\n</context>\n\n{message}"
    else:
        full_message = message

    progress.step("loading config...")
    config = _load_config()
    store = _get_store(config)
    project = _resolve_project(None, config)

    session_data = store.get_active_session(project)
    if not session_data:
        progress.done()
        print("No active session. Run 'phasectl start' first.", file=sys.stderr)
        raise typer.Exit(3)

    current_phase = _derive_current_phase(store, session_data)
    phase_config = load_phase(current_phase)

    turns = store.get_turns(session_data["id"])

    if should_compress(turns, phase_config.token_budget):
        progress.step(
            f"compressing tail turns via {_compression_label(config)}..."
        )
        try:
            compress_tail(session_data["id"], store, config)
        except AuthError as e:
            progress.done()
            _die_on_auth_error(e)
        except Exception as e:
            progress.done()
            print(f"Compression failed: {e}", file=sys.stderr)
            raise typer.Exit(2)
        turns = store.get_turns(session_data["id"])

    messages = []
    for turn in turns:
        if turn["compressed"]:
            continue
        role = "user" if turn["role"] == "system" else turn["role"]
        messages.append({"role": role, "content": turn["content"]})

    messages.append({"role": "user", "content": full_message})

    store.add_turn(
        session_id=session_data["id"],
        phase=current_phase,
        role="user",
        content=full_message,
        token_estimate=estimate_tokens(full_message),
    )

    provider_label = _provider_label(config)

    def _on_round(ix: int, tools_n: int) -> None:
        if ix == 0:
            tool_hint = f", {tools_n} tools available" if tools_n else ""
            progress.step(
                f"sending to {provider_label} ({current_phase} phase{tool_hint})..."
            )
        else:
            progress.step(f"sending follow-up to {provider_label}...")

    def _on_tool_call(name: str, args: dict) -> None:
        progress.event(f"[tool call] {_fmt_tool_call(name, args)}")

    try:
        response, _tools_available = api_chat(
            messages=messages,
            phase_config=phase_config,
            app_config=config,
            project=project,
            on_tool_call=_on_tool_call,
            on_round=_on_round,
        )
    except AuthError as e:
        progress.done()
        _die_on_auth_error(e)
    except Exception as e:
        progress.done()
        print(f"API error: {e}", file=sys.stderr)
        raise typer.Exit(2)

    progress.step("receiving response...")

    store.add_turn(
        session_id=session_data["id"],
        phase=current_phase,
        role="assistant",
        content=response,
        token_estimate=estimate_tokens(response),
    )

    all_turns = store.get_turns(session_data["id"])
    total = sum(t["token_estimate"] for t in all_turns)
    store.update_session(session_data["id"], total_tokens=total)

    progress.done()
    print(response)


@app.command()
def switch(
    phase: str = typer.Option(..., "--phase", help="Phase to switch to (orient|ideate|design|impl|validate|snapshot, or any user-defined phase in ~/.config/phasectl/phases)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the confirmation on stderr."),
):
    """Switch the active session to a different phase.

    Subsequent 'phasectl chat' calls will use the new phase's system
    prompt, temperature, token budget, and tool set. Prior turns are kept
    and remain visible to the model. Exit 3 if no session is active, 1 if
    the phase name is unknown.
    """
    config = _load_config()
    store = _get_store(config)
    project = _resolve_project(None, config)

    session_data = store.get_active_session(project)
    if not session_data:
        print("No active session. Run 'phasectl start' first.", file=sys.stderr)
        raise typer.Exit(3)

    try:
        phase_config = load_phase(phase)
    except FileNotFoundError:
        available = list_phases()
        print(
            f"Phase '{phase}' not found. "
            f"Available phases: {', '.join(available)}",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    store.update_session(session_data["id"], closing_phase=phase)

    _diag(
        f"Switched to {phase}. Budget: {phase_config.token_budget} tokens.\n"
        f"[{phase}] {phase_config.system_prompt[:150]}...",
        quiet=quiet,
    )


@app.command()
def close(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Reserved; snapshot output is a single line and is always printed."),
):
    """Close the active session and produce a snapshot summary.

    All uncompressed turns are handed to the 'snapshot' phase prompt via
    the compression model. The resulting summary is stored on the session
    row, and any RFC-<n> / DECISION-<n> tokens found in it are linked into
    the local session graph (~/.config/phasectl/graph.json).

    Prints '<session-id> <turns>→1 <tokens>t' on stdout.
    Exit 3 if no session is active, 2 if compression fails.
    """
    progress = Progress(quiet=quiet)
    progress.step("loading config...")
    config = _load_config()
    store = _get_store(config)
    project = _resolve_project(None, config)

    session_data = store.get_active_session(project)
    if not session_data:
        progress.done()
        print("No active session to close.", file=sys.stderr)
        raise typer.Exit(3)

    current_phase = _derive_current_phase(store, session_data)
    snapshot_phase = load_phase("snapshot")

    turns = store.get_uncompressed_turns(session_data["id"])
    turn_texts = [
        f"[{t['phase']}][{t['role']}]: {t['content']}" for t in turns
    ]
    turns_text = (
        "\n\n".join(turn_texts) if turn_texts else "No turns in this session."
    )

    progress.step(
        f"compressing {len(turns)} turns via {_compression_label(config)}..."
    )
    try:
        summary = api_compress(
            turns_text, config, system_prompt=snapshot_phase.system_prompt
        )
    except AuthError as e:
        progress.done()
        _die_on_auth_error(e)
    except Exception as e:
        progress.done()
        print(f"Compression failed: {e}", file=sys.stderr)
        raise typer.Exit(2)

    all_turns = store.get_turns(session_data["id"])
    total_tokens = sum(t["token_estimate"] for t in all_turns)

    progress.step("storing session snapshot...")
    close_session(store, session_data["id"], current_phase, summary, total_tokens)

    rfcs, decisions = extract_rfcs_and_decisions(summary)
    progress.step(
        f"updating graph ({len(rfcs)} RFCs, {len(decisions)} DECISIONs linked)..."
    )
    persist_session_graph(session_data["id"], project, summary, session_data["created_at"], config)

    progress.done()
    print(f"{session_data['id'][:8]} {len(turns)}→1 {total_tokens}t")


@app.command()
def status(
    json_flag: bool = typer.Option(False, "--json", help="Emit a JSON object with session_id, project, phase, tokens, turns (and last_session when no session is active)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Reserved; status output is already terse."),
):
    """Show whether a session is active for the default project.

    With an active session, prints its short id, phase, token estimate and
    turn count. With none, prints the date and short id of the most recent
    closed session, or 'No active session.' if there is no history.
    """
    config = _load_config()
    store = _get_store(config)
    project = _resolve_project(None, config)

    session_data = store.get_active_session(project)
    if session_data:
        current_phase = _derive_current_phase(store, session_data)
        turns = store.get_turns(session_data["id"])
        total_tokens = sum(t["token_estimate"] for t in turns)
        if json_flag:
            print(json.dumps({
                "session_id": session_data["id"][:8],
                "project": session_data["project"],
                "phase": current_phase,
                "tokens": total_tokens,
                "turns": len(turns),
            }))
        else:
            print(
                f"Project: {session_data['project']}\n"
                f"Phase: {current_phase}\n"
                f"Session: {session_data['id'][:8]}\n"
                f"Tokens: ~{total_tokens}\n"
                f"Turns: {len(turns)}"
            )
    else:
        last = store.get_last_session(project)
        if last:
            close_date = last.get("closed_at", "unknown")
            if json_flag:
                print(json.dumps({
                    "session_id": last["id"][:8],
                    "project": project,
                    "phase": None,
                    "tokens": last.get("total_tokens", 0),
                    "turns": 0,
                    "last_session": close_date[:10],
                }))
            else:
                print(
                    f"No active session.\n"
                    f"Last session: {close_date[:10]} ({last['id'][:8]})"
                )
        else:
            if json_flag:
                print(json.dumps({
                    "session_id": None,
                    "project": project,
                    "phase": None,
                    "tokens": 0,
                    "turns": 0,
                    "last_session": None,
                }))
            else:
                print("No active session.")


def _mcp_binary_from_config(config: dict) -> str:
    raw = ((config.get("tools") or {}).get("mcp_command") or "").strip()
    if not raw:
        raw = "jcodemunch-mcp serve"
    tokens = shlex.split(raw)
    return tokens[0] if tokens else ""


def _run_index(binary: str, path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binary, "index", path],
        capture_output=True, text=True, timeout=120,
    )


def _parse_symbol_count(stdout: str) -> str:
    """Extract a human summary from the indexer's stdout.

    Tries JSON first (looks for common count keys). Falls back to a
    single-line stdout tail.
    """
    text = (stdout or "").strip()
    if not text:
        return "indexed (no output from indexer)"

    def _first_int(d: dict, keys: tuple[str, ...]) -> int | None:
        for k in keys:
            v = d.get(k)
            if isinstance(v, int):
                return v
        return None

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            syms = _first_int(data, ("symbol_count", "symbols", "count"))
            files = _first_int(data, ("file_count", "files_count", "num_files"))
            if syms is not None and files is not None:
                return f"indexed: {syms} symbols across {files} files"
            if syms is not None:
                return f"indexed: {syms} symbols"
            if files is not None:
                return f"indexed: {files} files"
            msg = data.get("message") or data.get("status")
            if isinstance(msg, str):
                return f"indexed: {msg}"
    except (ValueError, TypeError):
        pass
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return f"indexed: {lines[-1]}" if lines else "indexed"


@app.command()
def index(
    project: str = typer.Option(None, "--project", "-p", help="Project name to store the index_path under, in config.toml. Defaults to defaults.project from config."),
    path: str = typer.Option(None, "--path", help="Absolute or relative path to the project root. If omitted, the previously stored path for --project is re-indexed."),
    register_only: bool = typer.Option(
        False, "--register-only",
        help="Store the path in config without invoking any indexer. For MCP servers that handle indexing externally.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the 'Indexing …' diagnostic on stderr."),
):
    """Index a project so 'phasectl query' and the impl/validate tools can find its symbols.

    Resolves the indexer binary from tools.mcp_command in config.toml
    (first token, e.g. 'jcodemunch-mcp' from 'jcodemunch-mcp serve') and
    invokes `<binary> index <abs_path>` as a subprocess. The resolved
    path is written to config.toml at projects.<name>.index_path.

    If --path is omitted and the project already has a stored path,
    that path is re-indexed. Use --register-only for MCP servers that
    do their own indexing externally.
    """
    config = _load_config()
    project = _resolve_project(project, config)

    if path is None:
        stored = get_index_path(project, config)
        if stored is None:
            print(
                f"no stored path for project '{project}'. Pass --path <path> for the first index.",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        path = str(stored)

    if not os.path.exists(path):
        print(f"Path does not exist: {path}", file=sys.stderr)
        raise typer.Exit(1)

    abs_path = os.path.abspath(path)

    if register_only:
        set_index_path(project, abs_path)
        print(f"registered: {project} → {abs_path}")
        return

    binary = _mcp_binary_from_config(config)
    if not binary:
        print(
            "no MCP indexer binary configured. Set [tools].mcp_command in config.toml "
            "or pass --register-only to store the path without indexing.",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    progress = Progress(quiet=quiet)
    progress.step(f"indexing {abs_path} via {binary}...")

    try:
        proc = _run_index(binary, abs_path)
    except FileNotFoundError:
        progress.done()
        print(
            f"indexer binary not found on PATH: {binary}. Install it, or pass "
            "--register-only to store the path without indexing.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        progress.done()
        print(f"{binary} index timed out after 120s", file=sys.stderr)
        raise typer.Exit(1)

    if proc.returncode != 0:
        progress.done()
        stderr_tail = (proc.stderr or "").strip().splitlines()
        hint = stderr_tail[-1] if stderr_tail else ""
        looks_unknown = (
            "unknown" in (proc.stderr or "").lower()
            or "usage" in (proc.stderr or "").lower()
            or "no such" in (proc.stderr or "").lower()
        )
        if looks_unknown:
            print(
                f"{binary} does not support indexing. Index your codebase manually, "
                f"then register: phasectl index --project {project} --path {abs_path} --register-only",
                file=sys.stderr,
            )
        else:
            print(f"{binary} index failed: {hint or proc.returncode}", file=sys.stderr)
        raise typer.Exit(1)

    summary = _parse_symbol_count(proc.stdout)
    progress.step("storing index path in config...")
    set_index_path(project, abs_path)
    progress.done()
    print(summary)


@app.command()
def query(
    symbol: str = typer.Argument(..., help="Symbol name or pattern to search for."),
    project: str = typer.Option(None, "--project", "-p", help="Project name whose stored index_path holds the index to search. Defaults to defaults.project from config."),
    tool: str = typer.Option(None, "--tool", help="MCP tool to invoke. Defaults to the first discovered tool whose name contains 'search'; if none but a dispatcher-style 'order' tool is present, wraps into `order(action=\"search_symbols\", args={...})`."),
    repo: str = typer.Option(None, "--repo", help="Repo identifier to pass to the MCP tool (e.g. 'owner/name'). Falls back to [projects.<name>].repo in config.toml; auto-derived from the git remote if absent."),
    json_flag: bool = typer.Option(False, "--json", help="Print the indexer's raw JSON result instead of one match per line."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Reserved; query output goes to stdout unchanged."),
):
    """Search the indexed symbols of a project via a discovered MCP tool.

    Requires a prior 'phasectl index --project <name> --path <dir>'.
    Exit 4 if the project has no stored index or if no suitable search
    tool can be found (pass --tool to override).
    """
    config = _load_config()
    project = _resolve_project(project, config)
    index_path = get_index_path(project, config)
    if not index_path:
        print(f"no index for {project}. run: phasectl index --project {project} --path <path>", file=sys.stderr)
        raise typer.Exit(4)
    resolved_repo = repo or _resolve_project_repo(project, config, str(index_path))

    tool_executor = ToolExecutor(project=project)
    try:
        discovered = tool_executor.discovered_tools(config)
        if not discovered:
            print("no tools discovered from MCP server", file=sys.stderr)
            raise typer.Exit(4)
        names = {t.get("name") for t in discovered}

        if tool:
            tool_name = tool
            args: dict = {"query": symbol}
            if resolved_repo:
                args["repo"] = resolved_repo
        else:
            tool_name = next(
                (t["name"] for t in discovered if "search" in (t.get("name") or "").lower()),
                None,
            )
            if tool_name:
                args = {"query": symbol}
                if resolved_repo:
                    args["repo"] = resolved_repo
            elif "order" in names:
                tool_name = "order"
                inner: dict = {"query": symbol}
                if resolved_repo:
                    inner["repo"] = resolved_repo
                args = {"action": "search_symbols", "args": inner}
            else:
                available = ", ".join(sorted(n for n in names if n))
                print(
                    f"no search tool discovered. Pass --tool NAME. Available: {available}",
                    file=sys.stderr,
                )
                raise typer.Exit(4)

        result = tool_executor.execute(tool_name, args, config=config)
    finally:
        tool_executor.close()

    if "ERROR" in result:
        print(result, file=sys.stderr)
        raise typer.Exit(4)

    if json_flag:
        print(result)
    else:
        for line in result.strip().split("\n"):
            if line.strip():
                print(line)


def _resolve_project_repo(project: str, config: dict, index_path: str) -> str:
    """Return the repo identifier for a project.

    Order: [projects.<name>].repo → git remote of index_path (owner/name).
    Empty string if unresolved.
    """
    projects = config.get("projects") or {}
    stored = (projects.get(project) or {}).get("repo", "")
    if stored:
        return stored
    if not index_path:
        return ""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", index_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=3,
        )
        url = result.stdout.strip()
        if not url:
            return ""
        # https://github.com/owner/repo.git or git@github.com:owner/repo.git
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com" in url:
            _, _, rest = url.partition("github.com")
            rest = rest.lstrip(":/").strip()
            if rest.count("/") >= 1:
                parts = rest.split("/")
                return "/".join(parts[-2:])
    except Exception:
        pass
    return ""


@app.command()
def graph(
    project: str = typer.Option(None, "--project", "-p", help="Project name to filter the graph on. Defaults to defaults.project from config."),
    json_flag: bool = typer.Option(False, "--json", help="Emit the graph as a JSON {sessions: [...]} document."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Reserved."),
):
    """Show closed sessions with their linked RFCs and DECISIONs.

    Reads ~/.config/phasectl/graph.json (a NetworkX MultiDiGraph on disk).
    RFC-<n> and DECISION-<n> tokens are extracted from each session's
    closing summary and attached to the session node. Standalone — no
    session needed. Prints nothing (or an empty JSON) when nothing has
    been closed yet.
    """
    from .config import get_graph_path
    from .graph import GraphStore

    config = _load_config()
    project = _resolve_project(project, config)
    graph_path = get_graph_path(config)

    if not os.path.exists(graph_path):
        if json_flag:
            print(json.dumps({"sessions": []}))
        else:
            print("(no sessions closed yet — graph will populate on first `phasectl close`)")
        return

    try:
        store = GraphStore(str(graph_path))
        result = store.get_project_graph(project)
        store.close()
    except Exception as e:
        print(f"Graph query failed: {e}", file=sys.stderr)
        raise typer.Exit(1)

    if json_flag:
        print(json.dumps(result))
    else:
        sessions = result.get("sessions", [])
        if not sessions:
            print("(empty graph)")
            return
        for s in sessions:
            print(f"Session {s['id']} ({s['created_at'][:10]})")
            for rfc in s.get("rfcs", []):
                print(f"  RFC: {rfc['id']} [{rfc['status']}] {rfc['title']}")
            for dec in s.get("decisions", []):
                print(f"  DECISION: {dec['id']} {dec['summary']}")


@app.command()
def loose(
    project: str = typer.Option(None, "--project", "-p", help="Project name; uses its stored index_path as the repo. Falls back to the default_project if omitted."),
    path: str = typer.Option(None, "--path", help="Path to any git repository. Bypasses --project and works on repos phasectl has never indexed."),
    base: str = typer.Option("main", "--base", help="Base branch to compare against. Falls back to 'master' or origin/HEAD if the requested branch doesn't exist."),
    json_flag: bool = typer.Option(False, "--json", help="Emit the raw collected data as JSON (repo, current_branch, base, uncommitted, unmerged, stashes, unpushed, worktrees)."),
    synthesize: bool = typer.Option(False, "--synthesize", help="Call the compression model to add one 'Most stale: …' top line naming the coldest loose thread. Requires the API key."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Drop the 'In flight across …' header line."),
):
    """List every loose thread in a git repo: uncommitted files, unmerged branches, stashes, unpushed commits, extra worktrees.

    Standalone — no session needed. Works on any git repo via --path;
    otherwise reads the project's stored index_path. Exits 1 if no repo
    can be resolved or the path is not a git repo. --synthesize is the
    only sub-feature that touches the API.
    """
    progress = Progress(quiet=quiet)
    progress.step("loading config...")
    config = _load_config()
    active_project = (
        (config.get("defaults", {}) or {}).get("project", "")
        or _infer_project_from_cwd(config)
        or ""
    )

    repo = loose_resolve_repo(path, project, active_project, config)
    if not repo:
        progress.done()
        print(
            "no repo: pass --path <dir> or index a project first (phasectl index --project <name> --path <dir>)",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    if not is_git_repo(repo):
        progress.done()
        print(f"not a git repository: {repo}", file=sys.stderr)
        raise typer.Exit(1)

    data = loose_collect(repo, base=base, progress=progress)

    if json_flag:
        progress.done()
        print(json.dumps(data))
        return

    if project:
        label = project
    elif path:
        label = os.path.basename(os.path.abspath(repo).rstrip("/")) or repo
    else:
        label = active_project or os.path.basename(os.path.abspath(repo).rstrip("/")) or repo

    top_line = ""
    if synthesize:
        progress.step(
            f"synthesizing 'Most stale' line via {_compression_label(config)}..."
        )
        top_line = loose_synthesize_top_line(data, config)

    rendered = loose_render_human(data, label)

    progress.done()
    if quiet:
        for line in rendered.splitlines()[1:]:
            print(line)
    else:
        if top_line:
            print(top_line)
        print(rendered)


@app.command()
def check(
    project: str = typer.Option(None, "--project", "-p", help="Project name for the index/git checks. Defaults to defaults.project from config."),
    json_flag: bool = typer.Option(False, "--json", help="Emit a JSON {ok, project, checks:[{name, ok, message}]} document."),
):
    """Smoke-test every subsystem. No LLM calls, no API key required.

    Reports pass/fail for each of:
      config  — config.toml exists and is readable
      auth    — an API key is discoverable (env or credentials file)
      db      — sessions.db opens; count of sessions
      phases  — every phase TOML loads (bundled + user overrides)
      index   — the project has a stored index_path and it exists
      mcp     — jcodemunch-mcp is reachable and completes the handshake
      git     — the indexed repo (or cwd) is a git working tree

    Each row is one of:
      ✓  configured and working
      —  not configured yet (a setup step the user hasn't taken)
      ✗  broken (a configured subsystem that failed)

    Exit 0 unless a ✗ appears. Every check catches its own errors and
    the command still runs the rest.
    """
    config = _load_config()
    if not project:
        default = (config.get("defaults", {}) or {}).get("project", "") or ""
        project = default or _infer_project_from_cwd(config) or ""

    # status: "ok" | "unconfigured" | "fail"
    results: list[tuple[str, str, str]] = []

    def _ok(name: str, msg: str) -> None:
        results.append((name, "ok", msg))

    def _unconfigured(name: str, msg: str) -> None:
        results.append((name, "unconfigured", msg))

    def _fail(name: str, msg: str) -> None:
        results.append((name, "fail", msg))

    # config
    try:
        cf = get_config_file()
        if cf.exists():
            _ok("config", str(cf).replace(str(os.path.expanduser("~")), "~"))
        else:
            _unconfigured("config", f"missing: {cf}")
    except Exception as e:
        _fail("config", f"error: {e}")

    # auth — presence only, no live API call. Keyless backends (ollama) skip.
    try:
        if _is_keyless(config):
            _ok("auth", f"not needed (backend={_backend_from_config(config)})")
        else:
            src = key_source()
            if src == "not set":
                _unconfigured("auth", "no key configured (run: phasectl auth set)")
            else:
                _ok("auth", f"from {src}")
    except Exception as e:
        _fail("auth", f"error: {e}")

    # db
    try:
        db_path = str(get_db_path())
        store = SQLiteStore(db_path)
        n = store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        _ok("db", f"{os.path.basename(db_path)} ({n} sessions)")
    except Exception as e:
        _fail("db", f"error: {e}")

    # phases
    try:
        names = list_phases()
        loaded: list[str] = []
        for pn in names:
            try:
                load_phase(pn)
                loaded.append(pn)
            except Exception:
                pass
        if loaded:
            _ok("phases", f"{len(loaded)} phases loaded ({','.join(loaded)})")
        else:
            _fail("phases", "no phases loadable")
    except Exception as e:
        _fail("phases", f"error: {e}")

    # index
    idx_path = None
    try:
        if not project:
            _unconfigured("index", "no project (run from a project dir or pass --project)")
        else:
            idx = get_index_path(project, config)
            if idx is None:
                _unconfigured("index", f"'{project}' has no stored path (run: phasectl index --project {project} --path <dir>)")
            elif not idx.exists():
                _fail("index", f"'{project}' path missing: {idx}")
            else:
                idx_path = str(idx)
                display = str(idx).replace(str(os.path.expanduser("~")), "~")
                _ok("index", f"{project} → {display}")
    except Exception as e:
        _fail("index", f"error: {e}")

    # mcp — spawn the configured MCP server and list its tools
    try:
        import shlex
        from .mcp_client import MCPClient
        raw_cmd = (config.get("tools") or {}).get("mcp_command", "") or "jcodemunch-mcp serve"
        cmd = shlex.split(raw_cmd)
        with MCPClient(cmd) as mc:
            tools = mc.list_tools()
        _ok("mcp", f"{cmd[0]} reachable ({len(tools)} tools discovered)")
    except Exception as e:
        _fail("mcp", f"MCP server unreachable: {e}")

    # git — prefer the indexed repo, fall back to cwd
    try:
        from .loose import is_git_repo, get_current_branch, get_uncommitted
        repo = idx_path or os.getcwd()
        if is_git_repo(repo):
            branch = get_current_branch(repo) or "detached"
            dirty = get_uncommitted(repo).get("count", 0)
            _ok("git", f"repo is git ({branch}, {dirty} dirty)")
        else:
            _unconfigured("git", f"not a git repo: {repo}")
    except Exception as e:
        _fail("git", f"error: {e}")

    any_fail = any(status == "fail" for _, status, _ in results)
    all_ok = not any_fail

    _MARKS = {"ok": "✓", "unconfigured": "—", "fail": "✗"}

    if json_flag:
        print(json.dumps({
            "ok": all_ok,
            "project": project or None,
            "checks": [
                {"name": n, "ok": status == "ok", "status": status, "message": m}
                for n, status, m in results
            ],
        }))
    else:
        for name, status, msg in results:
            print(f"{(name + ':'):<10}  {_MARKS[status]} {msg}")

    if any_fail:
        raise typer.Exit(1)


@auth_app.command("set")
def auth_set():
    """Read an API key from a hidden prompt and store it in the best-available backend.

    Storage preference (first available wins):
      1. macOS Keychain (via `security add-generic-password`)
      2. freedesktop secret-service on Linux (via `secret-tool store`)
      3. ~/.config/phasectl/credentials (chmod 600) — headless fallback

    An env var ($PHASECTL_API_KEY or $ANTHROPIC_API_KEY) still overrides
    stored keys at read time. Exit 1 on empty input or if the prompt is
    cancelled.
    """
    import getpass

    try:
        key = getpass.getpass("API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        raise typer.Exit(1)

    if not key:
        print("phasectl: no key entered.", file=sys.stderr)
        raise typer.Exit(1)

    where = save_api_key(key)
    if where in ("macOS Keychain", "freedesktop secret-service"):
        print(f"key stored in {where}")
    else:
        print(f"key stored at {where}")


@auth_app.command("status")
def auth_status():
    """Report which source phasectl would load the API key from.

    Does not validate the key against the API.
    """
    config = _load_config()
    src = key_source()
    if _is_keyless(config) and src == "not set":
        print(f"key: not needed (backend={_backend_from_config(config)})")
    elif src == "not set":
        print("key: not set")
    else:
        print(f"key: from {src}")


@auth_app.command("remove")
def auth_remove():
    """Delete the API key from every backend that has it.

    Removes from macOS Keychain, freedesktop secret-service, and the
    credentials file (whichever are present). Env vars are not touched.
    Exit 0 whether or not anything was removed.
    """
    where = remove_api_key()
    if where == "not set":
        print("key: nothing to remove")
    else:
        print(f"key removed from {where}")


if __name__ == "__main__":
    app()