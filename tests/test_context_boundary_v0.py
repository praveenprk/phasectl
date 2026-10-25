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
        role="system",
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
