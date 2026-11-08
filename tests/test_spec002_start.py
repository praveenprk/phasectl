"""SPEC-002 — `start`: session handoff context (RFC-001, DECISION-006)."""

import json

import pytest
from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.context import estimate_tokens
from phasectl.session import open_session
from phasectl.store import SQLiteStore

LIVE_GIT = {
    "branch": "feat/importer", "dirty": True, "dirty_count": 2,
    "last_commit": {"hash": "abc1234", "subject": "feat(importer): parse csv rows", "when": "now"},
}
AGENT_CAPS = "last messages, each cut to 1200 chars, at most 4000 chars"


def _forbid_models(monkeypatch):
    def no_model(*args, **kwargs):
        raise AssertionError("a model was called on a default path")

    for target in (
        "phasectl.cli.api_compress",
        "phasectl.cli.api_chat",
        "phasectl.context.api_compress",
        "phasectl.resume.compress",
        "phasectl.seed.compress",
    ):
        monkeypatch.setattr(target, no_model)


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
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: dict(LIVE_GIT))
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: None)
    monkeypatch.setattr("phasectl.cli.persist_session_graph", lambda *a, **k: None)
    return store, tmp_path


def _add(store, session_id, role, content, origin="__role__"):
    store.add_turn(
        session_id=session_id, phase="design", role=role, content=content,
        token_estimate=estimate_tokens(content), origin=role if origin == "__role__" else origin,
    )


def _closed_prior(store, turns, summary="MODEL SUMMARY mentioning DECISION-900"):
    session = open_session(store, "proj", "design")
    for role, content in turns:
        _add(store, session.id, role, content)
    store.close_session(session.id, "design", summary, 10)
    return session


def _start(*args, **kwargs):
    return CliRunner().invoke(app, ["start", *args], **kwargs)


def _stored(store, origin):
    active = store.get_active_session("proj")
    return [t["content"] for t in store.get_turns(active["id"]) if t["origin"] == origin]


def _decisions_header(session_id):
    return (
        f"PRIOR_DECISIONS (historical record, verbatim from session {session_id[:8]} turns; "
        "not verified against the current repository):"
    )


def _turns_header(session_id):
    return (
        f"PRIOR_LAST_TURNS (historical, verbatim from session {session_id[:8]}; "
        "not verified against the current repository):"
    )


def _agent_transcript(tmp_path, text="agent was editing importer.py, next: wire import"):
    path = tmp_path / "agent.jsonl"
    path.write_text(json.dumps({
        "type": "user", "timestamp": "2026-11-08T10:00:00",
        "message": {"role": "user", "content": text},
    }) + "\n")
    return str(path)


def test_inv_002_01_no_model_call_by_default(env, monkeypatch):
    store, tmp_path = env
    _closed_prior(store, [("user", "DECISION-001 sqlite"), ("assistant", "ok")])
    agent = _agent_transcript(tmp_path)
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: agent)
    seed = tmp_path / "notes.md"
    seed.write_text("seed body")
    _forbid_models(monkeypatch)

    result = _start("--phase", "orient", "--seed", str(seed))

    assert result.exit_code == 0, result.output
    assert _stored(store, "resume") == []
    assert _stored(store, "seed_gist") == []


def test_inv_002_02_prior_decisions_from_source_turns(env, monkeypatch):
    store, _ = env
    prior = _closed_prior(store, [
        ("user", "Plan:\n- DECISION-001 sqlite, not JSON\nnoise line"),
        ("assistant", "Confirmed DECISION-001 sqlite, not JSON"),
    ])
    _forbid_models(monkeypatch)

    result = _start("--phase", "design")

    assert result.exit_code == 0, result.output
    expected = (
        _decisions_header(prior.id)
        + "\n- [user] DECISION-001 sqlite, not JSON"
        + "\n- [assistant] Confirmed DECISION-001 sqlite, not JSON"
    )
    assert _stored(store, "prior_decisions") == [expected]
    assert expected in result.stdout


def test_inv_002_02_explicit_empty_states(env, monkeypatch):
    store, _ = env
    _forbid_models(monkeypatch)

    assert _start("--phase", "design").exit_code == 0
    assert _stored(store, "prior_decisions") == [
        "PRIOR_DECISIONS: unavailable (no closed session for this project)"
    ]
    assert CliRunner().invoke(app, ["close"]).exit_code == 0
    closed = store.get_last_session("proj")

    assert _start("--phase", "design").exit_code == 0
    assert _stored(store, "prior_decisions") == [
        f"PRIOR_DECISIONS: none recorded in session {closed['id'][:8]}"
    ]


def test_inv_002_03_prior_decisions_ignore_model_summary(env, monkeypatch):
    store, _ = env
    _closed_prior(store, [("user", "no decisions here")],
                  summary="- Model says DECISION-900 was made")
    _forbid_models(monkeypatch)

    assert _start("--phase", "design").exit_code == 0
    [block] = _stored(store, "prior_decisions")
    assert "DECISION-900" not in block
    assert block.startswith("PRIOR_DECISIONS: none recorded in session ")


def test_inv_002_04_decisions_chain_across_sessions(env, monkeypatch):
    store, _ = env
    _closed_prior(store, [("user", "DECISION-001 use SQLite"), ("assistant", "ok")])
    _forbid_models(monkeypatch)
    runner = CliRunner()

    assert runner.invoke(app, ["start", "--phase", "design"]).exit_code == 0
    assert runner.invoke(app, ["close"]).exit_code == 0
    assert runner.invoke(app, ["start", "--phase", "design"]).exit_code == 0

    [block] = _stored(store, "prior_decisions")
    assert "- [user] DECISION-001 use SQLite" in block.splitlines()


def test_inv_002_05_prior_turns_verbatim(env, monkeypatch):
    store, _ = env
    prior = _closed_prior(store, [
        ("user", "u1"), ("assistant", "a1"),
        ("user", "next step:\nimplement import"), ("assistant", "a2"),
    ])
    _forbid_models(monkeypatch)

    result = _start("--phase", "design")

    expected = _turns_header(prior.id) + "\n[user]: next step:\nimplement import\n[assistant]: a2"
    assert _stored(store, "prior_turns") == [expected]
    assert expected in result.stdout


def test_inv_002_05_explicit_empty_states(env, monkeypatch):
    store, _ = env
    _forbid_models(monkeypatch)
    assert _start("--phase", "design").exit_code == 0
    assert _stored(store, "prior_turns") == [
        "PRIOR_LAST_TURNS: unavailable (no closed session for this project)"
    ]
    assert CliRunner().invoke(app, ["close"]).exit_code == 0
    closed = store.get_last_session("proj")
    assert _start("--phase", "design").exit_code == 0
    assert _stored(store, "prior_turns") == [
        f"PRIOR_LAST_TURNS: none recorded in session {closed['id'][:8]}"
    ]


def test_inv_002_06_agent_context_verbatim_excerpt(env, monkeypatch):
    store, tmp_path = env
    agent = _agent_transcript(tmp_path)
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: agent)
    _forbid_models(monkeypatch)

    result = _start("--phase", "orient")

    assert result.exit_code == 0, result.output
    [stored] = _stored(store, "agent_context")
    header, body = stored.split("\n", 1)
    assert header == f"AGENT_CONTEXT (external, verbatim excerpt of {agent}: {AGENT_CAPS}; not verified):"
    assert "agent was editing importer.py, next: wire import" in body
    assert f"AGENT_CONTEXT: {len(body)} chars from {agent}" in result.stdout
    assert "agent was editing importer.py" not in result.stdout


@pytest.mark.parametrize("args", [["--phase", "orient", "--no-fuse"], ["--phase", "design"]])
def test_inv_002_06_no_agent_context_outside_fused_orient(env, monkeypatch, args):
    store, tmp_path = env
    agent = _agent_transcript(tmp_path)
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: agent)
    _forbid_models(monkeypatch)

    assert _start(*args).exit_code == 0
    assert _stored(store, "agent_context") == []


def test_inv_002_07_seed_verbatim(env, monkeypatch):
    store, tmp_path = env
    small = tmp_path / "notes.md"
    small.write_text("Seed body with DECISION-1 detail\nsecond line")
    big = tmp_path / "big.txt"
    big.write_text("y" * 250_000)
    _forbid_models(monkeypatch)

    result = _start("--phase", "design", "--seed", str(small), "--seed", str(big))

    assert result.exit_code == 0, result.output
    seeds = _stored(store, "seed")
    assert seeds[0] == "SEED notes.md (supplied, verbatim; not verified):\nSeed body with DECISION-1 detail\nsecond line"
    assert seeds[1].split("\n", 1)[0] == "SEED big.txt (supplied, verbatim; not verified; TRUNCATED to first 200KB):"
    assert seeds[1].split("\n", 1)[1] == "y" * 200_000


def test_inv_002_08_summarize_adds_labelled_turns_and_warns(env, monkeypatch):
    store, tmp_path = env
    prior = _closed_prior(store, [("user", "DECISION-001 sqlite"), ("assistant", "ok")])
    agent = _agent_transcript(tmp_path)
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: agent)
    seed = tmp_path / "notes.md"
    seed.write_text("seed body")
    monkeypatch.setattr("phasectl.resume.compress", lambda *a, **k: "RESUME TEXT")
    monkeypatch.setattr("phasectl.seed.compress", lambda *a, **k: "GIST TEXT")

    result = _start("--phase", "orient", "--seed", str(seed), "--summarize", "--quiet")

    assert result.exit_code == 0, result.output
    assert "warning: resume synthesis may cause signal loss due to compression" in result.stderr
    assert "warning: seed gist may cause signal loss due to compression" in result.stderr
    assert _stored(store, "resume") == ["RESUME TEXT"]
    assert _stored(store, "seed_gist") == ["GIST TEXT"]
    assert _stored(store, "prior_decisions") == [_decisions_header(prior.id) + "\n- [user] DECISION-001 sqlite"]
    assert _stored(store, "prior_turns") == [_turns_header(prior.id) + "\n[user]: DECISION-001 sqlite\n[assistant]: ok"]
    assert _stored(store, "seed") == ["SEED notes.md (supplied, verbatim; not verified):\nseed body"]
    assert len(_stored(store, "agent_context")) == 1
    assert "[MODEL-DERIVED RESUME (non-authoritative)]\nRESUME TEXT" in result.stdout


def test_inv_002_09_summarize_failure_is_reported_not_stored(env, monkeypatch):
    store, tmp_path = env
    _closed_prior(store, [("user", "DECISION-001 sqlite")])
    seed = tmp_path / "notes.md"
    seed.write_text("seed body")

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("phasectl.resume.compress", boom)
    monkeypatch.setattr("phasectl.seed.compress", boom)

    result = _start("--phase", "orient", "--seed", str(seed), "--summarize", "--quiet")

    assert result.exit_code == 0, result.output
    assert "resume synthesis failed: provider down" in result.stderr
    assert "seed gist failed for notes.md: provider down" in result.stderr
    assert _stored(store, "resume") == []
    assert _stored(store, "seed_gist") == []
    assert len(_stored(store, "seed")) == 1
    assert "[MODEL-DERIVED RESUME" not in result.stdout


def test_inv_002_11_refusals(env, monkeypatch):
    store, _ = env
    _forbid_models(monkeypatch)

    unknown = _start("--phase", "no-such-phase")
    assert unknown.exit_code == 1
    assert "Phase 'no-such-phase' not found." in unknown.stderr
    assert store.get_active_session("proj") is None

    assert _start("--phase", "design").exit_code == 0
    active = store.get_active_session("proj")
    turns_before = store.get_turns(active["id"])
    again = _start("--phase", "design")
    assert again.exit_code == 1
    assert "Session already active for project 'proj'. Close it first." in again.stderr
    assert store.get_active_session("proj")["id"] == active["id"]
    assert store.get_turns(active["id"]) == turns_before


def test_stdout_order(env, monkeypatch):
    store, tmp_path = env
    _closed_prior(store, [("user", "DECISION-001 sqlite"), ("assistant", "ok")])
    agent = _agent_transcript(tmp_path)
    monkeypatch.setattr("phasectl.cli.find_agent_session", lambda *a, **k: agent)
    _forbid_models(monkeypatch)

    out = _start("--phase", "orient").stdout
    positions = [out.index(marker) for marker in (
        "CURRENT_GIT_STATE:", "PRIOR_DECISIONS", "PRIOR_LAST_TURNS", "AGENT_CONTEXT:", "session opened:",
    )]
    assert positions == sorted(positions)
    assert out.rstrip("\n").splitlines()[-1].startswith("session opened: ")


def _git_repo(tmp_path):
    import subprocess

    repo = tmp_path / "live"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (repo / "cli.py").write_text("v1\n")
    run("add", ".")
    run("commit", "-q", "-m", "feat(importer): parse csv rows")
    (repo / "cli.py").write_text("v2\n")
    (repo / "notes.txt").write_text("n\n")
    for i in range(55):
        (repo / f"zz{i:02d}.txt").write_text("x\n")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()
    return repo, head


def test_inv_002_12_live_git_block_names_head_and_changes(env, monkeypatch, tmp_path):
    from phasectl.resume import get_git_state

    store, _ = env
    repo, head = _git_repo(tmp_path)
    monkeypatch.setattr("phasectl.cli.get_git_state", get_git_state)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, config: repo)
    _forbid_models(monkeypatch)

    out = _start("--phase", "orient").stdout

    lines = out.splitlines()
    i = lines.index(f"CURRENT_GIT_HEAD: {head} feat(importer): parse csv rows")
    assert lines[i - 1] == f"CURRENT_GIT_STATE: branch=main dirty=True dirty_count=57 head={head}"
    assert lines[i + 1] == "CURRENT_GIT_CHANGES (git status --porcelain, verbatim):"
    listed = lines[i + 2:i + 52]
    assert listed[0] == " M cli.py"
    assert "?? notes.txt" in listed
    assert lines[i + 52] == "… (7 more; run git status)"


def test_inv_002_12_clean_and_unavailable_states():
    from phasectl.handoff import format_live_git_block

    clean = {"branch": "main", "dirty": False, "dirty_count": 0, "status_lines": [],
             "last_commit": {"hash": "abc1234", "subject": "s", "when": "now"}}
    assert format_live_git_block(clean).splitlines()[1:] == [
        "CURRENT_GIT_HEAD: abc1234 s", "CURRENT_GIT_CHANGES: none",
    ]
    broken = {"branch": "main", "dirty": None, "dirty_count": None, "last_commit": None}
    assert format_live_git_block(broken).splitlines() == [
        "CURRENT_GIT_STATE: branch=main dirty=unavailable dirty_count=unavailable head=unavailable",
        "CURRENT_GIT_HEAD: unavailable",
        "CURRENT_GIT_CHANGES: unavailable",
    ]
    assert format_live_git_block({}) == "CURRENT_GIT_STATE: unavailable"
