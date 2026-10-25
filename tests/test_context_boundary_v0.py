import os

import pytest
from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.session import open_session
from phasectl.store import SQLiteStore
from phasectl.context import build_injection_block


LIVE_BRANCH = "docs/SPEC-032-caller-scope-binding"
HIST_BRANCH = "feat/RFC-028-scope-resolution-contract"
SECOND_BRANCH = "docs/SECOND-live-branch"
HEAD_COMMIT = "abc123def456"


def _chat_setup(tmp_path, monkeypatch, git_state):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    open_session(store, "proj", "orient")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    monkeypatch.setattr(
        "phasectl.cli._load_config",
        lambda: {
            "defaults": {"project": "proj"},
            "storage": {"db_path": str(db_path)},
            "projects": {"proj": {"index_path": str(repo_path)}},
        },
    )

    calls = []

    def fake_git_state(path):
        calls.append(path)
        if callable(git_state):
            return git_state()
        return git_state

    monkeypatch.setattr("phasectl.cli.get_git_state", fake_git_state)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, config: repo_path)

    captured = []

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return "assistant reply", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    return CliRunner(), calls, captured, repo_path, store


def test_chat_refreshes_git_state_at_invocation(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert calls == [repo_path]
    assert any(
        LIVE_BRANCH in str(message.get("content", ""))
        for message in captured[0]
    )


def test_chat_captures_fresh_git_state_on_each_invocation(tmp_path, monkeypatch):
    states = iter(
        [
            {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0},
            {"branch": SECOND_BRANCH, "dirty": True, "dirty_count": 1},
        ]
    )
    runner, calls, captured, repo_path, store = _chat_setup(tmp_path, monkeypatch, lambda: next(states))

    first = runner.invoke(app, ["chat", "first"])
    second = runner.invoke(app, ["chat", "second"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert calls == [repo_path, repo_path]
    assert any(LIVE_BRANCH in str(m.get("content", "")) for m in captured[0])
    assert any(SECOND_BRANCH in str(m.get("content", "")) for m in captured[1])
    assert not any(SECOND_BRANCH in str(m.get("content", "")) for m in captured[0])


def test_chat_reports_unavailable_git_state(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(tmp_path, monkeypatch, {})

    store.add_turn(
        session_id=store.get_active_session("proj")["id"],
        phase="orient",
        role="user",
        content=f"Historical turn mentions {HIST_BRANCH}.",
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert calls == [repo_path]
    assert any(
        "unavailable" in str(message.get("content", "")).lower()
        for message in captured[0]
    )
    assert any(
        HIST_BRANCH in str(message.get("content", ""))
        for message in captured[0]
    )


def test_prior_session_memory_disabled_retains_current_turns(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="assistant",
        content="CURRENT-SESSION-TURN",
        token_estimate=10,
    )

    prior_session = {
        "id": "prior-session",
        "final_summary": "PRIOR-SESSION-MEMORY",
    }
    prior_turns = [{"role": "assistant", "content": "PRIOR-TURN"}]
    monkeypatch.setattr(
        "phasectl.cli.load_prior_session",
        lambda store, project: (prior_session, prior_turns),
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert any("CURRENT-SESSION-TURN" in str(m.get("content", "")) for m in captured[0])
    assert not any("PRIOR-SESSION-MEMORY" in str(m.get("content", "")) for m in captured[0])
    assert not any("PRIOR-TURN" in str(m.get("content", "")) for m in captured[0])


def test_prior_session_memory_enabled_retains_current_turns(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="assistant",
        content="CURRENT-SESSION-TURN",
        token_estimate=10,
    )

    prior_session = {
        "id": "prior-session",
        "final_summary": "PRIOR-SESSION-MEMORY",
    }
    prior_turns = [{"role": "assistant", "content": "PRIOR-TURN"}]
    monkeypatch.setattr(
        "phasectl.cli.load_prior_session",
        lambda store, project: (prior_session, prior_turns),
    )

    result = runner.invoke(app, ["chat", "hello", "--prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert any("CURRENT-SESSION-TURN" in str(m.get("content", "")) for m in captured[0])
    assert any("PRIOR-SESSION-MEMORY" in str(m.get("content", "")) for m in captured[0])
    assert any("PRIOR-TURN" in str(m.get("content", "")) for m in captured[0])


def test_chat_authoritative_git_state_output_boundary(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return f"assistant reply: current branch is {HIST_BRANCH}", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert HIST_BRANCH in result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert f"CURRENT_GIT_STATE: branch={HIST_BRANCH}" not in result.output


def test_prior_session_memory_is_optional():
    assert build_injection_block(None, []) == ""


def test_no_prior_session_memory_excludes_injected_prior_memory(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="assistant",
        content="CURRENT-SESSION-TURN",
        token_estimate=10,
    )

    injected = build_injection_block(
        {"final_summary": "PRIOR-SESSION-MEMORY"},
        [{"role": "assistant", "content": "PRIOR-TURN"}],
    )
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="system",
        content=injected,
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert any("CURRENT-SESSION-TURN" in str(m.get("content", "")) for m in captured[0])
    assert not any("PRIOR-SESSION-MEMORY" in str(m.get("content", "")) for m in captured[0])
    assert not any("PRIOR-TURN" in str(m.get("content", "")) for m in captured[0])
    assert any(
        "Prior session summary:" in str(m.get("content", ""))
        for m in captured[0]
    ) is False


def test_chat_current_git_state_includes_live_head_commit(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path,
        monkeypatch,
        {
            "branch": LIVE_BRANCH,
            "dirty": False,
            "dirty_count": 0,
            "last_commit": {"hash": HEAD_COMMIT, "subject": "subject", "when": "now"},
        },
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert f"head={HEAD_COMMIT}" in result.output
    assert any(f"head={HEAD_COMMIT}" in str(m.get("content", "")) for m in captured[0])


def test_chat_unavailable_git_state_does_not_substitute_head(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(tmp_path, monkeypatch, {})

    store.add_turn(
        session_id=store.get_active_session("proj")["id"],
        phase="orient",
        role="system",
        content=f"Historical turn mentions head={HIST_BRANCH}.",
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert "CURRENT_GIT_STATE: unavailable" in result.output
    assert f"head={HIST_BRANCH}" not in result.output.split("CURRENT_GIT_STATE: unavailable", 1)[-1]


def test_chat_branch_present_status_failed_marks_dirty_unavailable(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert "dirty=unavailable" in result.output
    assert "dirty_count=unavailable" in result.output
    assert "dirty=False" not in result.output
    assert "dirty_count=0" not in result.output


def test_chat_branch_present_head_failed_does_not_fabricate_head(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert "head=unavailable" in result.output


def test_git_state_head_lookup_failure_is_explicit(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0, "last_commit": None}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert "head=unavailable" in result.output


def test_start_unavailable_working_tree_status_displays_unavailable(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    monkeypatch.setattr(
        "phasectl.cli._load_config",
        lambda: {
            "defaults": {"project": "proj"},
            "storage": {"db_path": str(db_path)},
            "projects": {"proj": {"index_path": str(repo_path)}},
        },
    )
    monkeypatch.setattr(
        "phasectl.cli.get_git_state",
        lambda path: {"branch": LIVE_BRANCH, "dirty": None, "dirty_count": None},
    )
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, config: repo_path)
    monkeypatch.setattr("phasectl.resume.compress", lambda *a, **k: "resume")

    runner = CliRunner()
    result = runner.invoke(app, ["start", "--phase", "orient"])

    assert result.exit_code == 0, result.output
    assert "unavailable dirty" in result.output
    assert "None dirty" not in result.output


def test_chat_records_turn_origins(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    turns = store.get_turns(store.get_active_session("proj")["id"])
    user_turns = [t for t in turns if t["role"] == "user" and t["content"] == "hello"]
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert user_turns and user_turns[-1]["origin"] == "user"
    assert assistant_turns and assistant_turns[-1]["origin"] == "assistant"


def test_prior_session_memory_filters_by_origin_without_markers(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="system",
        content="HISTORICAL-BY-ORIGIN",
        token_estimate=10,
        origin="prior_session",
    )

    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert not any("HISTORICAL-BY-ORIGIN" in str(m.get("content", "")) for m in captured[0])


def test_legacy_unknown_origin_rows_survive_migration(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            created_at TEXT NOT NULL,
            closed_at TEXT,
            opening_phase TEXT NOT NULL,
            closing_phase TEXT,
            final_summary TEXT,
            total_tokens INTEGER DEFAULT 0
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            compressed INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.execute(
        "INSERT INTO turns (session_id, phase, role, content, token_estimate, timestamp, compressed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", "orient", "system", "LEGACY-CONTENT", 5, "2026-01-01T00:00:00", 0),
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    store2 = SQLiteStore(str(db_path))

    turns = store.get_turns("s1") if False else None
    cursor = store.conn.execute("SELECT * FROM turns WHERE session_id='s1'")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "LEGACY-CONTENT"
    assert rows[0]["origin"] is None

    columns = {r["name"] for r in store.conn.execute("PRAGMA table_info(turns)").fetchall()}
    assert "origin" in columns


def test_legacy_unknown_system_row_historical_looking_excluded(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="system",
        content="Prior session summary: LEGACY-HISTORICAL",
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert not any("LEGACY-HISTORICAL" in str(m.get("content", "")) for m in captured[0])


def test_legacy_unknown_system_row_ordinary_looking_excluded(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="system",
        content="ORDINARY-LEGACY-SYSTEM-TURN",
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert not any("ORDINARY-LEGACY-SYSTEM-TURN" in str(m.get("content", "")) for m in captured[0])


def test_legacy_unknown_conversation_turn_remains_available(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    current_session = store.get_active_session("proj")
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="user",
        content="LEGACY-USER-TURN",
        token_estimate=10,
    )
    store.add_turn(
        session_id=current_session["id"],
        phase="orient",
        role="assistant",
        content="LEGACY-ASSISTANT-TURN",
        token_estimate=10,
    )

    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    assert any("LEGACY-USER-TURN" in str(m.get("content", "")) for m in captured[0])
    assert any("LEGACY-ASSISTANT-TURN" in str(m.get("content", "")) for m in captured[0])


def test_chat_spoofed_model_current_state_does_not_replace_cli_observation(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0, "last_commit": {"hash": HEAD_COMMIT, "subject": "s", "when": "now"}}
    )

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return f"assistant reply: CURRENT_GIT_STATE: branch={HIST_BRANCH} head=deadbeef", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in result.output
    assert f"head={HEAD_COMMIT}" in result.output
    assert "assistant reply: CURRENT_GIT_STATE" in result.output
    assert f"CURRENT_GIT_STATE: branch={HIST_BRANCH}" not in result.output.split("assistant reply", 1)[0]
