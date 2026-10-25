"""Focused regression: compressed turns must remain visible to close()."""

from pathlib import Path

import pytest

from phasectl.cli import close
from phasectl.session import open_session
from phasectl.store import SQLiteStore
from phasectl.context import estimate_tokens


def test_close_includes_compressed_original_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "testproj", "design")

    design_turn_id = store.add_turn(
        session_id=session.id,
        phase="design",
        role="user",
        content="Design decision: DECISION-TEST-001 use append-only turn storage.",
        token_estimate=estimate_tokens("Design decision: DECISION-TEST-001 use append-only turn storage."),
    )

    # Simulate compress_tail(): original turn is marked compressed, and a
    # lossy intermediate summary is stored in its place.
    store.mark_turns_compressed([design_turn_id])
    store.add_turn(
        session_id=session.id,
        phase="_compression",
        role="system",
        content="[COMPRESSED SUMMARY: prior design work; details omitted]",
        token_estimate=estimate_tokens("[COMPRESSED SUMMARY: prior design work; details omitted]"),
    )
    store.add_turn(
        session_id=session.id,
        phase="design",
        role="assistant",
        content="Continuing with implementation details.",
        token_estimate=estimate_tokens("Continuing with implementation details."),
    )

    captured: dict[str, str] = {}

    def fake_compress(turns_text: str, config: dict, system_prompt: str | None = None) -> str:
        captured["turns_text"] = turns_text
        return "SNAPSHOT WITHOUT IDENTIFIER"

    monkeypatch.setattr("phasectl.cli._load_config", lambda: {
        "storage": {"db_path": str(db_path)},
        "defaults": {"project": "testproj"},
    })
    monkeypatch.setattr("phasectl.cli._get_store", lambda config: store)
    monkeypatch.setattr("phasectl.cli._resolve_project", lambda project, config: "testproj")
    monkeypatch.setattr("phasectl.cli.api_compress", fake_compress)
    monkeypatch.setattr("phasectl.cli.persist_session_graph", lambda *args, **kwargs: None)

    close(quiet=True)

    assert "DECISION-TEST-001" in captured["turns_text"], (
        "close() passed only uncompressed turns to the snapshot compressor, "
        "so the original design decision is absent from the captured input:\n"
        + captured.get("turns_text", "<no captured input>")
    )


def test_close_excludes_synthetic_compression_summaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "testproj", "design")

    original_content = "Original design evidence."
    original_turn_id = store.add_turn(
        session_id=session.id,
        phase="design",
        role="user",
        content=original_content,
        token_estimate=estimate_tokens(original_content),
    )
    store.mark_turns_compressed([original_turn_id])
    store.add_turn(
        session_id=session.id,
        phase="_compression",
        role="system",
        content="Synthetic intermediate summary.",
        token_estimate=estimate_tokens("Synthetic intermediate summary."),
    )
    later_content = "Later implementation evidence."
    store.add_turn(
        session_id=session.id,
        phase="impl",
        role="assistant",
        content=later_content,
        token_estimate=estimate_tokens(later_content),
    )
    rows_before_close = store.get_turns(session.id)

    captured: dict[str, str] = {}

    def fake_compress(turns_text: str, config: dict, system_prompt: str | None = None) -> str:
        captured["turns_text"] = turns_text
        return "SNAPSHOT"

    monkeypatch.setattr("phasectl.cli._load_config", lambda: {
        "storage": {"db_path": str(db_path)},
        "defaults": {"project": "testproj"},
    })
    monkeypatch.setattr("phasectl.cli._get_store", lambda config: store)
    monkeypatch.setattr("phasectl.cli._resolve_project", lambda project, config: "testproj")
    monkeypatch.setattr("phasectl.cli.api_compress", fake_compress)
    monkeypatch.setattr("phasectl.cli.persist_session_graph", lambda *args, **kwargs: None)

    close(quiet=True)

    assert captured["turns_text"] == (
        f"[design][user]: {original_content}\n\n"
        f"[impl][assistant]: {later_content}"
    )
    assert store.get_turns(session.id) == rows_before_close
    assert store.get_turns(session.id)[0]["compressed"] == 1
