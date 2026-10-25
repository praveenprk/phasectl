"""Focused regression: compressed turns must remain visible to close()."""

from pathlib import Path

import pytest

from phasectl.api import extract_rfcs_and_decisions
from phasectl.cli import close
from phasectl.session import open_session
from phasectl.store import SQLiteStore
from phasectl.context import estimate_tokens
from phasectl.graph import extract_decision_references


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
    synthetic_content = "Synthetic intermediate summary with DECISION-SYNTHETIC-1."
    store.add_turn(
        session_id=session.id,
        phase="_compression",
        role="system",
        content=synthetic_content,
        token_estimate=estimate_tokens(synthetic_content),
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
    assert store.get_session(session.id)["final_summary"] == "SNAPSHOT"


def test_close_recovers_missing_reference_and_persists_identical_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "testproj", "design")
    original_content = "DECISION-C-903 was proposed and rejected during review."
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
        content="[COMPRESSED SUMMARY: decision reference omitted]",
        token_estimate=estimate_tokens("[COMPRESSED SUMMARY: decision reference omitted]"),
    )
    rows_before_close = store.get_turns(session.id)
    generated_summary = "SNAPSHOT WITHOUT IDENTIFIER"
    graph_calls: list[str] = []

    monkeypatch.setattr("phasectl.cli._load_config", lambda: {
        "storage": {"db_path": str(db_path)},
        "defaults": {"project": "testproj"},
    })
    monkeypatch.setattr("phasectl.cli._get_store", lambda config: store)
    monkeypatch.setattr("phasectl.cli._resolve_project", lambda project, config: "testproj")
    monkeypatch.setattr(
        "phasectl.cli.api_compress",
        lambda *args, **kwargs: generated_summary,
    )
    monkeypatch.setattr(
        "phasectl.cli.persist_session_graph",
        lambda session_id, project, summary, created_at, config: graph_calls.append(summary),
    )

    close(quiet=True)

    expected_summary = (
        generated_summary
        + "\n\nDecision references recovered from original turns (historical "
        "occurrence only; not acceptance, implementation, or verification):\n"
        "- DECISION-C-903"
    )
    stored_summary = store.get_session(session.id)["final_summary"]
    assert stored_summary == expected_summary
    assert graph_calls == [stored_summary]
    assert store.get_turns(session.id) == rows_before_close
    assert "Recovered decision references missing from generated summary: DECISION-C-903" in capsys.readouterr().err


def test_decision_reference_scanner_excludes_code_and_embedded_identifiers() -> None:
    text = """
```python
DECISION-100
```
~~~text
DECISION-101
~~~
`DECISION-102` and ``DECISION-103``
`multiline inline code:
DECISION-108
end inline code`
XDECISION-104 FOO-DECISION-105 DECISION-106_suffix DECISION-107-extra
Ordinary prose about choosing append-only storage has no explicit reference.
DECISION-333 was proposed and rejected. DECISION-333 remains only a historical reference.
"""

    assert extract_decision_references(text) == ["DECISION-333"]
    assert extract_rfcs_and_decisions(text) == ([], ["DECISION-333"])


def test_close_deduplicates_recovery_and_preserves_complete_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    graph_calls: list[str] = []
    complete_summary = "\nEligible prose: DECISION-C-903, DECISION-A-100, and DECISION-019  \n"
    generated_summaries = iter([
        "Code-only reference: `multiline\nDECISION-C-903\ncode`\nEligible prose: DECISION-019",
        complete_summary,
    ])

    monkeypatch.setattr("phasectl.cli._load_config", lambda: {
        "storage": {"db_path": str(db_path)},
        "defaults": {"project": "testproj"},
    })
    monkeypatch.setattr("phasectl.cli._get_store", lambda config: store)
    monkeypatch.setattr("phasectl.cli._resolve_project", lambda project, config: "testproj")
    monkeypatch.setattr(
        "phasectl.cli.api_compress",
        lambda *args, **kwargs: next(generated_summaries),
    )
    monkeypatch.setattr(
        "phasectl.cli.persist_session_graph",
        lambda session_id, project, summary, created_at, config: graph_calls.append(summary),
    )

    first_session = open_session(store, "testproj", "design")
    for content in (
        "Historical occurrence: DECISION-C-903.",
        "Duplicate: DECISION-C-903. Later reference: DECISION-A-100. Numeric reference: DECISION-019.",
    ):
        store.add_turn(
            session_id=first_session.id,
            phase="design",
            role="assistant",
            content=content,
            token_estimate=estimate_tokens(content),
        )

    close(quiet=True)

    first_summary = store.get_session(first_session.id)["final_summary"]
    assert first_summary.count("- DECISION-C-903") == 1
    assert first_summary.count("DECISION-019") == 1
    assert first_summary.endswith(
        "Decision references recovered from original turns (historical "
        "occurrence only; not acceptance, implementation, or verification):\n"
        "- DECISION-C-903\n"
        "- DECISION-A-100"
    )

    second_session = open_session(store, "testproj", "design")
    content = "References: DECISION-C-903, DECISION-A-100, and DECISION-019."
    store.add_turn(
        session_id=second_session.id,
        phase="design",
        role="assistant",
        content=content,
        token_estimate=estimate_tokens(content),
    )

    close(quiet=True)

    second_summary = store.get_session(second_session.id)["final_summary"]
    assert second_summary == complete_summary
    assert graph_calls == [first_summary, second_summary]
