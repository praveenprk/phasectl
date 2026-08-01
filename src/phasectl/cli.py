import sys
import json
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
    get_claude_code_projects_dir,
)
from .tools import ToolExecutor
from .resume import (
    get_git_state,
    find_claude_code_session,
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
from .auth import AuthError, key_source, save_api_key


app = typer.Typer(
    help=(
        "phasectl — a cognitive-mode manager for engineering sessions.\n\n"
        "Each session runs under a named phase (orient, ideate, design, impl, "
        "validate, snapshot) with its own system prompt, temperature, and token "
        "budget. Turns are stored in SQLite; closed sessions are summarised and "
        "linked in a local graph. Symbol lookup is delegated to an MCP-speaking "
        "code indexer (default: jcodemunch-mcp)."
    ),
    epilog="Run 'phasectl COMMAND --help' for details on any command.",
    no_args_is_help=True,
)
auth_app = typer.Typer(
    help="Manage the Anthropic API key (env or ~/.config/phasectl/credentials).",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


def _diag(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def _die_on_auth_error(exc: AuthError) -> None:
    print(str(exc), file=sys.stderr)
    raise typer.Exit(exc.exit_code)


def _load_config() -> dict:
    config_file = get_config_file()
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            '[api]\n'
            'model = "claude-sonnet-4-6"\n'
            'compression_model = "claude-haiku-4-5-20251001"\n\n'
            '[storage]\n'
            'db_path = ""\n\n'
            '[graph]\n'
            'path = ""\n\n'
            '[defaults]\n'
            'project = "contextos"\n'
        )
    with open(config_file, "rb") as f:
        return tomllib.load(f)


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
    project: str = typer.Option("contextos", "--project", "-p", help="Project name; scopes sessions, index path, and graph."),
    phase: str = typer.Option("orient", "--phase", help="Phase to open in (orient|ideate|design|impl|validate|snapshot; user overrides in ~/.config/phasectl/phases also accepted)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the diagnostic lines on stderr; the session id and any resume block still print on stdout."),
    no_fuse: bool = typer.Option(
        False, "--no-fuse", help="Skip the orient-phase fusion of git state + the last Claude Code transcript. The last phasectl session summary is still injected.",
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
    of the most recent Claude Code transcript for that repo (if one is
    found under ~/.claude/projects/…). Any --seed files are compressed and
    added as extra system context.
    """
    config = _load_config()
    store = _get_store(config)

    active = store.get_active_session(project)
    if active:
        print(
            f"Session already active for project '{project}'. Close it first.",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    try:
        load_phase(phase)
    except FileNotFoundError:
        available = list_phases()
        print(
            f"Phase '{phase}' not found. "
            f"Available phases: {', '.join(available)}",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    prior = load_prior_session(store, project)
    resume_block = ""
    if phase == "orient" and not no_fuse:
        session_summary = ""
        if prior:
            session_summary = (prior[0].get("final_summary") or "").strip()

        repo_path_obj = get_index_path(project, config)
        repo_path = str(repo_path_obj) if repo_path_obj else ""
        git_state: dict = {}
        cc_context = ""
        if repo_path:
            try:
                git_state = get_git_state(repo_path)
            except Exception as e:
                _diag(f"[orient] git introspection skipped: {e}", quiet=quiet)
            try:
                cc_dir = get_claude_code_projects_dir(config)
                cc_path = find_claude_code_session(repo_path, cc_dir)
                if cc_path:
                    cc_context = extract_last_context(cc_path)
            except Exception as e:
                _diag(f"[orient] Claude Code lookup skipped: {e}", quiet=quiet)
        else:
            _diag(
                f"[orient] no indexed path for '{project}' — fusion limited to session summary.",
                quiet=quiet,
            )

        try:
            resume_block = build_resume_block(git_state, session_summary, cc_context, config)
        except Exception as e:
            _diag(f"[orient] synthesis failed: {e}", quiet=quiet)
            resume_block = ""

    if prior:
        session_data, turns = prior
        summary = session_data.get("final_summary", "") or ""
        close_phase = session_data.get("closing_phase", "unknown") or "unknown"
        close_date = session_data.get("closed_at", "unknown")
        if close_date != "unknown":
            close_date = close_date[:16]
        if not resume_block:
            _diag(
                f"Prior session: {close_date}. Phase at close: {close_phase}.\n"
                f"Summary: {summary}",
                quiet=quiet,
            )
        else:
            _diag(
                f"Prior session: {close_date}. Phase at close: {close_phase}.",
                quiet=quiet,
            )

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
        _diag(
            f"No prior session found for project '{project}'. Starting fresh.",
            quiet=quiet,
        )
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

    if resume_block:
        print(resume_block)
    print(f"session opened: {session.id[:8]} {phase}")


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send. Wrap in quotes for anything longer than one word."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the 'Compressing tail turns…' diagnostic on stderr."),
):
    """Send a message in the active session and print Claude's reply.

    Uses the current phase's system prompt, temperature, and token budget.
    If the running token estimate exceeds the phase budget, older turns are
    compressed into a summary before the request. In the impl and validate
    phases the model can call code-lookup tools (search_symbols,
    get_symbol_source, get_file_outline, get_ranked_context,
    get_blast_radius, get_untested_symbols) served by jcodemunch-mcp; the
    tool loop runs until the model produces a plain text reply.

    Exit codes: 3 if no session is active, 2 for other API errors,
    1 or 2 for auth errors (see 'phasectl auth --help').
    """
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if not session_data:
        print("No active session. Run 'phasectl start' first.", file=sys.stderr)
        raise typer.Exit(3)

    current_phase = _derive_current_phase(store, session_data)
    phase_config = load_phase(current_phase)

    turns = store.get_turns(session_data["id"])

    if should_compress(turns, phase_config.token_budget):
        _diag("Compressing tail turns...", quiet=quiet)
        try:
            compress_tail(session_data["id"], store, config)
        except AuthError as e:
            _die_on_auth_error(e)
        except Exception as e:
            print(f"Compression failed: {e}", file=sys.stderr)
            raise typer.Exit(2)
        turns = store.get_turns(session_data["id"])

    messages = []
    for turn in turns:
        if turn["compressed"]:
            continue
        role = "user" if turn["role"] == "system" else turn["role"]
        messages.append({"role": role, "content": turn["content"]})

    messages.append({"role": "user", "content": message})

    store.add_turn(
        session_id=session_data["id"],
        phase=current_phase,
        role="user",
        content=message,
        token_estimate=estimate_tokens(message),
    )

    system_prompt = phase_config.system_prompt
    try:
        response = api_chat(
            messages=messages,
            phase_config=phase_config,
            app_config=config,
            project=project,
        )
    except AuthError as e:
        _die_on_auth_error(e)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        raise typer.Exit(2)

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
    project = config["defaults"]["project"]

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
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if not session_data:
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

    try:
        summary = api_compress(
            turns_text, config, system_prompt=snapshot_phase.system_prompt
        )
    except AuthError as e:
        _die_on_auth_error(e)
    except Exception as e:
        print(f"Compression failed: {e}", file=sys.stderr)
        raise typer.Exit(2)

    all_turns = store.get_turns(session_data["id"])
    total_tokens = sum(t["token_estimate"] for t in all_turns)

    close_session(store, session_data["id"], current_phase, summary, total_tokens)

    persist_session_graph(session_data["id"], project, summary, session_data["created_at"], config)

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
    project = config["defaults"]["project"]

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


@app.command()
def index(
    project: str = typer.Option("contextos", "--project", "-p", help="Project name to store the index_path under, in config.toml."),
    path: str = typer.Option(None, "--path", help="Absolute or relative path to the project root. If omitted, the previously stored path for --project is reused."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the 'Indexing …' diagnostic on stderr."),
):
    """Index a project so 'phasectl query' and the impl/validate tools can find its symbols.

    Requires 'jcodemunch-mcp' on PATH; it is invoked over stdio to perform
    the indexing. The resolved absolute path is written to config.toml at
    [projects.<name>].index_path so subsequent runs can be spelled just
    'phasectl index --project <name>' (or, when <name> matches the
    default_project, plain 'phasectl index'). First-time indexing requires
    --path. Exit 1 on missing path, unknown project, or indexer error.
    """
    if path is None:
        stored = get_index_path(project)
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
    _diag(f"Indexing {abs_path} as {project}...", quiet=quiet)

    tool_executor = ToolExecutor(project=project)
    try:
        result = tool_executor.execute("index_project", {"path": abs_path})
    finally:
        tool_executor.close()

    if "ERROR" in result:
        print(result, file=sys.stderr)
        raise typer.Exit(1)

    set_index_path(project, abs_path)
    print(result)


@app.command()
def query(
    symbol: str = typer.Argument(..., help="Symbol name or pattern to search for (matched by the indexer's search_symbols action)."),
    project: str = typer.Option("contextos", "--project", "-p", help="Project name whose stored index_path holds the index to search."),
    json_flag: bool = typer.Option(False, "--json", help="Print the indexer's raw JSON result instead of one match per line."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Reserved; query output goes to stdout unchanged."),
):
    """Search the indexed symbols of a project. Standalone — no session needed.

    Requires a prior 'phasectl index --project <name> --path <dir>'.
    Exit 4 if the project has no stored index or the indexer reports an
    error.
    """
    index_path = get_index_path(project)
    if not index_path:
        print(f"no index for {project}. run: phasectl index --project {project} --path <path>", file=sys.stderr)
        raise typer.Exit(4)

    tool_executor = ToolExecutor(project=project)
    try:
        result = tool_executor.execute("search_symbols", {"query": symbol})
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


@app.command()
def graph(
    project: str = typer.Option("contextos", "--project", "-p", help="Project name to filter the graph on."),
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
    config = _load_config()
    active_project = config.get("defaults", {}).get("project", "")

    repo = loose_resolve_repo(path, project, active_project, config)
    if not repo:
        print(
            "no repo: pass --path <dir> or index a project first (phasectl index --project <name> --path <dir>)",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    if not is_git_repo(repo):
        print(f"not a git repository: {repo}", file=sys.stderr)
        raise typer.Exit(1)

    data = loose_collect(repo, base=base)

    if json_flag:
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
        top_line = loose_synthesize_top_line(data, config)

    rendered = loose_render_human(data, label)

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

    Every check catches its own errors; a failing one prints ✗ with a
    reason and the command still runs the rest. Exit 0 if all pass, 1 if
    any fail.
    """
    config = _load_config()
    project = project or config.get("defaults", {}).get("project", "") or ""

    results: list[tuple[str, bool, str]] = []

    def _add(name: str, ok: bool, msg: str) -> None:
        results.append((name, ok, msg))

    # config
    try:
        cf = get_config_file()
        if cf.exists():
            _add("config", True, str(cf).replace(str(os.path.expanduser("~")), "~"))
        else:
            _add("config", False, f"missing: {cf}")
    except Exception as e:
        _add("config", False, f"error: {e}")

    # auth — presence only, no live API call
    try:
        src = key_source()
        if src == "not set":
            _add("auth", False, "no key configured (run: phasectl auth set)")
        else:
            _add("auth", True, f"{src} (set)")
    except Exception as e:
        _add("auth", False, f"error: {e}")

    # db
    try:
        db_path = str(get_db_path())
        store = SQLiteStore(db_path)
        n = store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        _add("db", True, f"{os.path.basename(db_path)} ({n} sessions)")
    except Exception as e:
        _add("db", False, f"error: {e}")

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
            _add("phases", True, f"{len(loaded)} phases loaded ({','.join(loaded)})")
        else:
            _add("phases", False, "no phases loadable")
    except Exception as e:
        _add("phases", False, f"error: {e}")

    # index
    idx_path = None
    try:
        if not project:
            _add("index", False, "no project (set defaults.project or pass --project)")
        else:
            idx = get_index_path(project, config)
            if idx is None:
                _add("index", False, f"'{project}' has no stored path (run: phasectl index --project {project} --path <dir>)")
            elif not idx.exists():
                _add("index", False, f"'{project}' path missing: {idx}")
            else:
                idx_path = str(idx)
                display = str(idx).replace(str(os.path.expanduser("~")), "~")
                _add("index", True, f"{project} → {display}")
    except Exception as e:
        _add("index", False, f"error: {e}")

    # mcp — spawn jcodemunch-mcp and complete the handshake
    try:
        from .mcp_client import MCPClient
        with MCPClient(["jcodemunch-mcp", "serve"]):
            pass
        _add("mcp", True, "jcodemunch-mcp reachable (serve handshake ok)")
    except Exception as e:
        _add("mcp", False, f"jcodemunch-mcp unreachable: {e}")

    # git — prefer the indexed repo, fall back to cwd
    try:
        from .loose import is_git_repo, get_current_branch, get_uncommitted
        repo = idx_path or os.getcwd()
        if is_git_repo(repo):
            branch = get_current_branch(repo) or "detached"
            dirty = get_uncommitted(repo).get("count", 0)
            _add("git", True, f"repo is git ({branch}, {dirty} dirty)")
        else:
            _add("git", False, f"not a git repo: {repo}")
    except Exception as e:
        _add("git", False, f"error: {e}")

    all_ok = all(ok for _, ok, _ in results)

    if json_flag:
        print(json.dumps({
            "ok": all_ok,
            "project": project or None,
            "checks": [{"name": n, "ok": ok, "message": m} for n, ok, m in results],
        }))
    else:
        for name, ok, msg in results:
            mark = "✓" if ok else "✗"
            print(f"{(name + ':'):<10}  {mark} {msg}")

    if not all_ok:
        raise typer.Exit(1)


@auth_app.command("set")
def auth_set():
    """Read an Anthropic API key from a hidden prompt and store it at ~/.config/phasectl/credentials (chmod 600).

    The credentials file is consulted only when $ANTHROPIC_API_KEY is
    unset; if the env var is present it wins. Exit 1 on empty input or if
    the prompt is cancelled.
    """
    import getpass

    try:
        key = getpass.getpass("Anthropic API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        raise typer.Exit(1)

    if not key:
        print("phasectl: no key entered.", file=sys.stderr)
        raise typer.Exit(1)

    path = save_api_key(key)
    print(f"key stored at {path}")


@auth_app.command("status")
def auth_status():
    """Report which source phasectl would load the API key from.

    Prints one of: 'key: from environment', 'key: configured (credentials
    file)', or 'key: not set'. Does not validate the key against the API.
    """
    src = key_source()
    if src == "environment":
        print("key: from environment")
    elif src == "credentials file":
        print("key: configured (credentials file)")
    else:
        print("key: not set")


if __name__ == "__main__":
    app()