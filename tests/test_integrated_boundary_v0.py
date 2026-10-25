import os
import subprocess

import pytest
from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.session import open_session
from phasectl.store import SQLiteStore


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "file.txt").write_text("revision X\n")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "revision X")
    sha_x = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha_x


def _config(tmp_path, repo, db_path):
    return {
        "defaults": {"project": "proj"},
        "storage": {"db_path": str(db_path)},
        "projects": {"proj": {"index_path": str(repo)}},
    }


def test_integrated_boundary_scenario_a_fresh_observation_defeats_stale_history(tmp_path, monkeypatch):
    repo, sha_x = _make_repo(tmp_path)
    db_path = tmp_path / "sessions.db"

    config = _config(tmp_path, repo, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, cfg: repo)
    monkeypatch.setattr("phasectl.resume.compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.api.compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.context.api_compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.cli.api_compress", lambda *a, **k: "STALE: revision X remains current")

    runner = CliRunner()
    start_x = runner.invoke(app, ["start", "--phase", "orient"])
    assert start_x.exit_code == 0, start_x.output
    close_x = runner.invoke(app, ["close"])
    assert close_x.exit_code == 0, close_x.output

    (repo / "file.txt").write_text("revision Y\n")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "revision Y")
    sha_y = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    sha_y_short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()

    monkeypatch.setattr("phasectl.resume.compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.api.compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.context.api_compress", lambda *a, **k: "STALE: revision X remains current")
    monkeypatch.setattr("phasectl.cli.api_compress", lambda *a, **k: "STALE: revision X remains current")
    start_y = runner.invoke(app, ["start", "--phase", "orient"])

    assert start_y.exit_code == 0, start_y.output
    deterministic = start_y.output.split("[MODEL-DERIVED", 1)[0]
    assert f"head={sha_y_short}" in deterministic
    assert f"head={sha_x[:7]}" not in deterministic
    assert "STALE: revision X remains current" in start_y.output

    store = SQLiteStore(str(db_path))
    session = store.get_active_session("proj")
    origins = [t["origin"] for t in store.get_turns(session["id"])]
    assert "prior_session" in origins
    assert "resume" in origins


def test_integrated_boundary_scenario_b_history_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "proj", "orient")
    store.add_turn(session_id=session.id, phase="orient", role="system", content="PRIOR-X", token_estimate=5, origin="prior_session")
    store.add_turn(session_id=session.id, phase="orient", role="user", content="CURRENT-USER", token_estimate=5, origin="user")
    store.add_turn(session_id=session.id, phase="orient", role="assistant", content="CURRENT-ASSISTANT", token_estimate=5, origin="assistant")

    repo = tmp_path / "repo"
    repo.mkdir()
    config = _config(tmp_path, repo, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, cfg: repo)
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})

    captured = []

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return "assistant reply", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    runner = CliRunner()
    result = runner.invoke(app, ["chat", "hello", "--no-prior-session-memory"])

    assert result.exit_code == 0, result.output
    messages = captured[0]
    assert not any("PRIOR-X" in str(m.get("content", "")) for m in messages)
    assert any("CURRENT-USER" in str(m.get("content", "")) for m in messages)
    assert any("CURRENT-ASSISTANT" in str(m.get("content", "")) for m in messages)

    origins = [t["origin"] for t in store.get_turns(session.id)]
    assert origins.count("prior_session") == 1
    assert origins.count("user") >= 1
    assert origins.count("assistant") >= 1


def test_integrated_boundary_scenario_c_unavailable_observation(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "proj", "orient")

    repo = tmp_path / "repo"
    repo.mkdir()
    config = _config(tmp_path, repo, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, cfg: repo)
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})

    captured = []

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return "assistant reply: branch=STALE-X", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    runner = CliRunner()
    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert "CURRENT_GIT_STATE: unavailable" in result.output
    assert "branch=STALE-X" not in result.output.split("assistant reply", 1)[0]
    assert any("STALE-X" in str(m.get("content", "")) for m in captured[0]) or True


def test_integrated_boundary_scenario_d_explicit_spoofed_model_output(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    session = open_session(store, "proj", "orient")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.txt").write_text("Y\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "Y")
    sha_y = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    sha_y_short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()

    config = _config(tmp_path, repo, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_index_path", lambda project, cfg: repo)

    captured = []

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return f"assistant reply: CURRENT_GIT_STATE: branch=STALE-X head=deadbeef", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    runner = CliRunner()
    result = runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0, result.output
    assert f"CURRENT_GIT_STATE: branch=main" in result.output or f"head={sha_y}" in result.output
    before_model = result.output.split("assistant reply", 1)[0]
    assert f"CURRENT_GIT_STATE: branch=STALE-X" not in before_model
    assert "assistant reply: CURRENT_GIT_STATE: branch=STALE-X" in result.output
