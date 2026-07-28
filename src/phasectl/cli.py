import typer
import tomllib

from .store import SQLiteStore
from .session import open_session, close_session, load_prior_session
from .phases import load_phase, list_phases
from .context import estimate_tokens, should_compress, compress_tail, build_injection_block
from .api import chat as api_chat, compress as api_compress
from .config import get_config_file, get_db_path


app = typer.Typer()


def _load_config() -> dict:
    config_file = get_config_file()
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            '[api]\n'
            'model = "claude-sonnet-4-6"\n'
            'compression_model = "claude-haiku-3-5"\n\n'
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
):
    """Start a new session."""
    config = _load_config()
    store = _get_store(config)

    active = store.get_active_session(project)
    if active:
        typer.echo(
            f"Session already active for project '{project}'. Close it first."
        )
        raise typer.Exit(1)

    try:
        load_phase(phase)
    except FileNotFoundError:
        available = list_phases()
        typer.echo(
            f"Phase '{phase}' not found. "
            f"Available phases: {', '.join(available)}"
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
        typer.echo(
            f"Prior session: {close_date}. Phase at close: {close_phase}.\n"
            f"Summary: {summary}"
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
        typer.echo(
            f"No prior session found for project '{project}'. Starting fresh."
        )
        session = open_session(store, project, phase)

    typer.echo(f"Session {session.id[:8]} started in phase '{phase}'.")


@app.command()
def chat(
    message: str = typer.Argument(..., help="Your message to Claude"),
):
    """Send a message in the current session."""
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if not session_data:
        typer.echo("No active session. Run 'phasectl start' first.")
        raise typer.Exit(1)

    current_phase = _derive_current_phase(store, session_data)
    phase_config = load_phase(current_phase)

    turns = store.get_turns(session_data["id"])

    if should_compress(turns, phase_config.token_budget):
        compress_tail(session_data["id"], store, config)
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
    response = api_chat(
        messages=messages,
        system_prompt=system_prompt,
        temperature=phase_config.temperature,
        app_config=config,
    )

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

    typer.echo(response)


@app.command()
def switch(
    phase: str = typer.Option(..., "--phase", help="Phase to switch to"),
):
    """Switch the current phase."""
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if not session_data:
        typer.echo("No active session. Run 'phasectl start' first.")
        raise typer.Exit(1)

    try:
        phase_config = load_phase(phase)
    except FileNotFoundError:
        available = list_phases()
        typer.echo(
            f"Phase '{phase}' not found. "
            f"Available phases: {', '.join(available)}"
        )
        raise typer.Exit(1)

    store.update_session(session_data["id"], closing_phase=phase)

    typer.echo(
        f"Switched to {phase}. Budget: {phase_config.token_budget} tokens.\n"
        f"[{phase}] {phase_config.system_prompt[:150]}..."
    )


@app.command()
def close():
    """Close the current session with a snapshot compression."""
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if not session_data:
        typer.echo("No active session to close.")
        raise typer.Exit(1)

    current_phase = _derive_current_phase(store, session_data)
    snapshot_phase = load_phase("snapshot")

    turns = store.get_uncompressed_turns(session_data["id"])
    turn_texts = [
        f"[{t['phase']}][{t['role']}]: {t['content']}" for t in turns
    ]
    turns_text = (
        "\n\n".join(turn_texts) if turn_texts else "No turns in this session."
    )

    summary = api_compress(
        turns_text, config, system_prompt=snapshot_phase.system_prompt
    )

    all_turns = store.get_turns(session_data["id"])
    total_tokens = sum(t["token_estimate"] for t in all_turns)

    close_session(store, session_data["id"], current_phase, summary, total_tokens)

    typer.echo(
        f"Compressed {len(turns)} turns -> 1 summary (haiku). "
        f"Stored session {session_data['id'][:8]}.\n"
        f"Total tokens this session: ~{total_tokens}. Goodbye."
    )


@app.command()
def status():
    """Show current session status."""
    config = _load_config()
    store = _get_store(config)
    project = config["defaults"]["project"]

    session_data = store.get_active_session(project)
    if session_data:
        current_phase = _derive_current_phase(store, session_data)
        turns = store.get_turns(session_data["id"])
        total_tokens = sum(t["token_estimate"] for t in turns)
        typer.echo(
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
            typer.echo(
                f"No active session.\n"
                f"Last session: {close_date[:10]} ({last['id'][:8]})"
            )
        else:
            typer.echo("No active session.")


if __name__ == "__main__":
    app()
