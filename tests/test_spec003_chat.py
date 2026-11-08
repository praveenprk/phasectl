"""SPEC-003 — `chat`: context assembly and compression (RFC-001, DECISION-006)."""

import pytest
from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.context import estimate_tokens
from phasectl.phases import load_phase
from phasectl.session import open_session
from phasectl.store import SQLiteStore

SYSTEM_ORIGINS = ("prior_decisions", "prior_turns", "agent_context", "seed", "resume")


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    config = {
        "defaults": {"project": "proj"},
        "storage": {"db_path": str(db_path)},
        "projects": {"proj": {"index_path": str(repo)}},
    }
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: None)
    monkeypatch.setattr("phasectl.cli.persist_session_graph", lambda *a, **k: None)

    sent = []

    def fake_chat(**kwargs):
        sent.append(kwargs["messages"])
        return "assistant reply", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)
    return store, sent


def _forbid_compression(monkeypatch):
    def no_model(*args, **kwargs):
        raise AssertionError("compression was called without --compress")

    monkeypatch.setattr("phasectl.context.api_compress", no_model)
    monkeypatch.setattr("phasectl.cli.api_compress", no_model)


def _add(store, session_id, role, content, origin="__role__"):
    store.add_turn(
        session_id=session_id, phase="design", role=role, content=content,
        token_estimate=estimate_tokens(content), origin=role if origin == "__role__" else origin,
    )


def _over_budget_session(store, with_system=False):
    session = open_session(store, "proj", "design")
    budget = load_phase("design").token_budget
    if with_system:
        for origin in SYSTEM_ORIGINS:
            _add(store, session.id, "system", f"{origin} block " + "s" * (budget * 2), origin=origin)
    for i in range(4):
        _add(store, session.id, "user", f"u{i} " + "x" * (budget * 2))
        _add(store, session.id, "assistant", f"a{i} " + "y" * (budget * 2))
    return session, budget


def _chat(*args, **kwargs):
    return CliRunner().invoke(app, ["chat", *args], **kwargs)


def test_inv_003_01_no_compression_by_default(env, monkeypatch):
    store, sent = env
    session, _ = _over_budget_session(store)
    _forbid_compression(monkeypatch)

    result = _chat("hello")

    assert result.exit_code == 0, result.output
    assert all(t["compressed"] == 0 for t in store.get_turns(session.id))
    assert not any(t["origin"] == "compression" for t in store.get_turns(session.id))


@pytest.mark.parametrize("quiet", [[], ["--quiet"]])
def test_inv_003_02_over_budget_warns_and_sends_all(env, monkeypatch, quiet):
    store, sent = env
    session, budget = _over_budget_session(store)
    turns_before = store.get_turns(session.id)
    total = sum(t["token_estimate"] for t in turns_before)
    _forbid_compression(monkeypatch)

    result = _chat("hello", *quiet)

    assert result.exit_code == 0, result.output
    assert (
        f"warning: context ~{total} tokens exceeds the design budget of {budget}; "
        "nothing was compressed. Use --compress (may cause signal loss due to compression) "
        "or close and start a new session."
    ) in result.stderr
    assert len(sent[0]) == len(turns_before) + 2


def test_inv_003_03_compress_flag_warns(env, monkeypatch):
    store, sent = env
    session, _ = _over_budget_session(store)
    monkeypatch.setattr("phasectl.context.api_compress", lambda *a, **k: "SUMMARY")

    result = _chat("hello", "--compress", "--quiet")

    assert result.exit_code == 0, result.output
    assert "warning: tail compression may cause signal loss due to compression" in result.stderr
    turns = store.get_turns(session.id)
    conversation = [t for t in turns if t["origin"] in ("user", "assistant")]
    compressed = [t["content"][:2] for t in conversation if t["compressed"]]
    assert compressed == ["u0", "a0", "u1", "a1", "u2", "a2"]
    assert any(t["origin"] == "compression" for t in turns)


def test_inv_003_04_compression_never_touches_system_turns(env, monkeypatch):
    store, sent = env
    session, _ = _over_budget_session(store, with_system=True)
    monkeypatch.setattr("phasectl.context.api_compress", lambda *a, **k: "SUMMARY")

    result = _chat("hello", "--compress", "--quiet")

    assert result.exit_code == 0, result.output
    turns = store.get_turns(session.id)
    for origin in SYSTEM_ORIGINS:
        [turn] = [t for t in turns if t["origin"] == origin]
        assert turn["compressed"] == 0
        assert turn["content"].startswith(f"{origin} block ")
    contents = [m["content"] for m in sent[0]]
    for origin in SYSTEM_ORIGINS:
        assert any(f"{origin} block " in c for c in contents)


def test_inv_003_05_prior_turns_included_by_default(env, monkeypatch):
    store, sent = env
    prior = open_session(store, "proj", "design")
    _add(store, prior.id, "user", "next step: implement import")
    _add(store, prior.id, "assistant", "agreed")
    store.close_session(prior.id, "design", "MODEL PROSE SHOULD STAY OUT", 10)
    _forbid_compression(monkeypatch)
    runner = CliRunner()

    assert runner.invoke(app, ["start", "--phase", "design"]).exit_code == 0
    assert runner.invoke(app, ["chat", "where were we?"]).exit_code == 0

    contents = [m["content"] for m in sent[0]]
    assert any("PRIOR_LAST_TURNS" in c and "[user]: next step: implement import" in c for c in contents)
    assert not any("MODEL PROSE SHOULD STAY OUT" in c for c in contents)


def test_inv_003_07_stdin_truncation_visible_to_model(env, monkeypatch):
    store, sent = env
    open_session(store, "proj", "design")
    _forbid_compression(monkeypatch)

    result = _chat("summarize this", input="z" * 13_000)

    assert result.exit_code == 0, result.output
    last = sent[0][-1]["content"]
    assert "[stdin truncated: first 12000 of 13000 chars]" in last
    assert "z" * 12_000 in last
    assert "z" * 12_001 not in last


def test_inv_003_08_no_active_session(env):
    result = _chat("hello")
    assert result.exit_code == 3
    assert "No active session. Run 'phasectl start' first." in result.stderr


def test_inv_003_09_chat_sends_full_live_git_block(env, monkeypatch):
    store, sent = env
    open_session(store, "proj", "design")
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {
        "branch": "feat/importer", "dirty": True, "dirty_count": 2,
        "status_lines": [" M cli.py", "?? notes.txt"],
        "last_commit": {"hash": "189dad5", "subject": "feat(importer): parse csv rows", "when": "now"},
    })
    _forbid_compression(monkeypatch)

    result = _chat("where are we?")

    block = (
        "CURRENT_GIT_STATE: branch=feat/importer dirty=True dirty_count=2 head=189dad5\n"
        "CURRENT_GIT_HEAD: 189dad5 feat(importer): parse csv rows\n"
        "CURRENT_GIT_CHANGES (git status --porcelain, verbatim):\n"
        " M cli.py\n"
        "?? notes.txt"
    )
    assert result.exit_code == 0, result.output
    assert sent[0][-2]["content"] == block
    assert result.stdout.startswith(block + "\n")
