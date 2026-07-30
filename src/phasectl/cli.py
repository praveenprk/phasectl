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


app = typer.Typer()


def _diag(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


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
            'window_tokens = 15000\n'
            'keep_last_turns = 3\n'
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
    project: str = typer.Option("contextos", "--project", "-p", help="Project name"),
    phase: str = typer.Option("orient", "--phase", help="Starting phase"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
    no_fuse: bool = typer.Option(
        False, "--no-fuse", help="Skip git + Claude Code fusion in orient (session summary only)"
    ),
    seed: list[str] = typer.Option(
        None, "--seed", help="Prime the session with a text file's gist (repeatable)"
    ),
):
    """Start a new session."""
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
    message: str = typer.Argument(..., help="Your message to Claude"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Send a message in the current session."""
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
    phase: str = typer.Option(..., "--phase", help="Phase to switch to"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Switch the current phase."""
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
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Close the current session with a snapshot compression."""
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
    json_flag: bool = typer.Option(False, "--json", help="Output JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Show current session status."""
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
    project: str = typer.Option("contextos", "--project", "-p", help="Project name"),
    path: str = typer.Option(..., "--path", help="Path to project root"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Index a project for symbol search via jCodeMunch."""
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
    symbol: str = typer.Argument(..., help="Symbol name or pattern to search for"),
    project: str = typer.Option("contextos", "--project", "-p", help="Project name"),
    json_flag: bool = typer.Option(False, "--json", help="Output JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Query symbols via jCodeMunch (no active session required)."""
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
    project: str = typer.Option("contextos", "--project", "-p", help="Project name"),
    json_flag: bool = typer.Option(False, "--json", help="Output JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress diagnostic output"),
):
    """Query session graph from Kuzu (no active session required)."""
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
    project: str = typer.Option(None, "--project", "-p", help="Project name (uses stored index path)"),
    path: str = typer.Option(None, "--path", help="Path to any git repo (bypasses --project)"),
    base: str = typer.Option("main", "--base", help="Base branch to compare against"),
    json_flag: bool = typer.Option(False, "--json", help="Emit collected data as JSON"),
    synthesize: bool = typer.Option(False, "--synthesize", help="Add one LLM-synthesized top line naming the most stale thread"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the header line and diagnostics"),
):
    """Surface every loose thread in a repo: stashes, unmerged branches, unpushed commits, worktrees, uncommitted work."""
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


if __name__ == "__main__":
    app()