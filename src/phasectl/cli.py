import sys
import json
import typer
import tomllib

from .store import SQLiteStore
from .session import open_session, close_session, load_prior_session
from .phases import load_phase, list_phases
from .context import estimate_tokens, should_compress, compress_tail, build_injection_block
from .api import chat as api_chat, compress as api_compress
from .config import get_config_file, get_db_path


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
    if prior:
        session_data, turns = prior
        summary = session_data.get("final_summary", "") or ""
        close_phase = session_data.get("closing_phase", "unknown") or "unknown"
        close_date = session_data.get("closed_at", "unknown")
        if close_date != "unknown":
            close_date = close_date[:16]
        _diag(
            f"Prior session: {close_date}. Phase at close: {close_phase}.\n"
            f"Summary: {summary}",
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
            system_prompt=system_prompt,
            temperature=phase_config.temperature,
            app_config=config,
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


if __name__ == "__main__":
    app()
