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


def test_prior_session_memory_is_optional():
    assert build_injection_block(None, []) == ""


@pytest.mark.xfail(
    strict=True,
    reason=(
        "chat has no observable prior-session selection boundary; minimal interface "
        "needed: optional prior memory passed to message assembly as a separately "
        "labeled historical block."
    ),
)
def test_chat_prior_session_memory_is_selectable_separately(tmp_path, monkeypatch):
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
        "closing_phase": "orient",
        "closed_at": "2026-01-01T00:00:00",
    }
    prior_turns = [{"role": "assistant", "content": "PRIOR-TURN"}]
    monkeypatch.setattr(
        "phasectl.cli.load_prior_session",
        lambda store, project: (prior_session, prior_turns),
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert any("CURRENT-SESSION-TURN" in str(m.get("content", "")) for m in captured[0])
    assert any("HISTORICAL_PRIOR_SESSION_MEMORY" in str(m.get("content", "")) for m in captured[0])
    assert any("PRIOR-SESSION-MEMORY" in str(m.get("content", "")) for m in captured[0])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "chat has no authoritative Git-state boundary; minimal interface needed: "
        "a deterministic current-state message/field separate from synthesized prose."
    ),
)
def test_chat_authoritative_git_state_survives_false_llm_claim(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return f"assistant reply: current branch is {HIST_BRANCH}", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert any("AUTHORITATIVE_CURRENT_GIT_STATE" in str(m.get("content", "")) for m in captured[0])
    assert any(LIVE_BRANCH in str(m.get("content", "")) for m in captured[0])
    assert HIST_BRANCH not in "".join(
        str(m.get("content", ""))
        for m in captured[0]
        if "AUTHORITATIVE_CURRENT_GIT_STATE" in str(m.get("content", ""))
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The public chat context is unstructured prose; minimal interface needed: "
        "a labeled authoritative current-state boundary distinguishable from "
        "historical and synthesized narrative."
    ),
)
def test_chat_exposes_structured_authoritative_boundary(tmp_path, monkeypatch):
    runner, calls, captured, repo_path, store = _chat_setup(
        tmp_path, monkeypatch, {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    )

    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert any(
        f"AUTHORITATIVE_CURRENT_GIT_STATE: branch={LIVE_BRANCH}" in str(m.get("content", ""))
        for m in captured[0]
    )
