"""SPEC-001 — `close`: deterministic session snapshot (RFC-001, DECISION-006)."""

import re

import pytest
from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.context import estimate_tokens
from phasectl.session import open_session
from phasectl.store import SQLiteStore

WARNING = "warning: snapshot summary may cause signal loss due to compression"
MODEL_LABEL = "[MODEL-DERIVED SUMMARY (non-authoritative)]"


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
        "projects": {"proj": {"index_path": str(repo)}, "other": {"index_path": str(repo)}},
    }
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})
    graph_calls = []
    monkeypatch.setattr(
        "phasectl.cli.persist_session_graph",
        lambda session_id, project, summary, created_at, config: graph_calls.append(summary),
    )
    return store, config, graph_calls


def _add(store, session_id, role, content, origin="__role__", phase="design"):
    if origin == "__role__":
        origin = role
    return store.add_turn(
        session_id=session_id, phase=phase, role=role, content=content,
        token_estimate=estimate_tokens(content), origin=origin,
    )


def _section(summary, name, next_name):
    start = summary.index(name)
    end = summary.index(next_name) if next_name else len(summary)
    return summary[start:end].rstrip("\n").splitlines()


def _close(*args):
    return CliRunner().invoke(app, ["close", *args])


def test_inv_001_01_no_model_call_by_default(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "user", "We pick SQLite. DECISION-001")
    _add(store, session.id, "assistant", "Noted.")
    _forbid_models(monkeypatch)

    result = _close()

    assert result.exit_code == 0, result.output
    summary = store.get_session(session.id)["final_summary"]
    assert summary.startswith(f"SNAPSHOT {session.id[:8]} (deterministic, verbatim from 2 stored turns)")


def test_inv_001_02_decision_lines_verbatim_ordered_deduplicated(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "system",
         "PRIOR_DECISIONS (historical record, verbatim from session aaaaaaaa turns; "
         "not verified against the current repository):\n- [user] DECISION-000 carried from before",
         origin="prior_decisions")
    long_tail = "x" * 500
    _add(store, session.id, "user",
         "Intro without refs\n- DECISION-002 cents, never floats\n"
         f"also DECISION-001 sqlite {long_tail}\n"
         "```\nDECISION-999 inside a fence\n```\n"
         "**Bold** line with DECISION-003 kept as is")
    _add(store, session.id, "assistant", "Confirmed: DECISION-002 cents, never floats")
    _add(store, session.id, "user", "* DECISION-002 cents, never floats")
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    summary = store.get_session(session.id)["final_summary"]

    assert _section(summary, "DECISION_LINES:", "REFERENCES:") == [
        "DECISION_LINES:",
        "- [user] DECISION-000 carried from before",
        "- [user] DECISION-002 cents, never floats",
        f"- [user] also DECISION-001 sqlite {long_tail}",
        "- [user] **Bold** line with DECISION-003 kept as is",
        "- [assistant] Confirmed: DECISION-002 cents, never floats",
    ]
    assert "DECISION-999" not in summary.split("LAST_TURNS:")[0]


def test_inv_001_03_compressed_turns_still_sourced(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    early = _add(store, session.id, "user", "Early decision: DECISION-007 append-only turns")
    store.mark_turns_compressed([early])
    _add(store, session.id, "system", "[COMPRESSED SUMMARY: details omitted]",
         origin="compression", phase="_compression")
    _add(store, session.id, "assistant", "Continuing.")
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    summary = store.get_session(session.id)["final_summary"]

    assert "- [user] Early decision: DECISION-007 append-only turns" in summary
    assert "REFERENCES: DECISION-007" in summary
    assert summary.endswith(
        "LAST_TURNS:\n[user]: Early decision: DECISION-007 append-only turns\n[assistant]: Continuing."
    )


def test_inv_001_04_references_complete(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "user", "See RFC-003 and DECISION-010.\n```\nRFC-998\n```")
    _add(store, session.id, "assistant", "`RFC-999` is code; DECISION-011 and RFC-003 again")
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    summary = store.get_session(session.id)["final_summary"]
    assert "REFERENCES: RFC-003, DECISION-010, DECISION-011\n" in summary


def test_inv_001_05_last_turns_verbatim(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    for role, content in [("user", "u1"), ("assistant", "a1"),
                          ("user", "line one\n\nline two"), ("assistant", "a2 final")]:
        _add(store, session.id, role, content)
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    summary = store.get_session(session.id)["final_summary"]
    assert summary.endswith("LAST_TURNS:\n[user]: line one\n\nline two\n[assistant]: a2 final")


def test_inv_001_05_empty_session_is_explicit(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    assert store.get_session(session.id)["final_summary"] == (
        f"SNAPSHOT {session.id[:8]} (deterministic, verbatim from 0 stored turns)\n"
        "DECISION_LINES: none\n"
        "REFERENCES: none\n"
        "LAST_TURNS: none"
    )


def test_inv_001_06_non_source_turns_ignored(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    for origin, ref in [("prior_session", "DECISION-050"), ("resume", "DECISION-051"),
                        ("seed", "DECISION-052"), ("agent_context", "DECISION-054"),
                        ("prior_turns", "DECISION-055"), ("seed_gist", "DECISION-058"),
                        (None, "DECISION-056")]:
        _add(store, session.id, "system", f"context mentioning {ref}", origin=origin)
    _add(store, session.id, "system", "[COMPRESSED SUMMARY: DECISION-053]",
         origin="compression", phase="_compression")
    _add(store, session.id, "user", "legacy row DECISION-057", origin=None)
    _forbid_models(monkeypatch)

    assert _close().exit_code == 0
    summary = store.get_session(session.id)["final_summary"]
    for ref in ("DECISION-050", "DECISION-051", "DECISION-052", "DECISION-053",
                "DECISION-054", "DECISION-055", "DECISION-056", "DECISION-058"):
        assert ref not in summary
    assert "- [user] legacy row DECISION-057" in summary
    assert "REFERENCES: DECISION-057\n" in summary


def _seed_pair(store):
    sessions = {}
    for project in ("proj", "other"):
        session = open_session(store, project, "design")
        _add(store, session.id, "user", "We pick SQLite. DECISION-001 and RFC-002")
        _add(store, session.id, "assistant", "Noted DECISION-001.")
        sessions[project] = session
    return sessions


def test_inv_001_07_summarize_appends_labelled_and_warns(env, monkeypatch):
    store, config, _ = env
    sessions = _seed_pair(store)
    _forbid_models(monkeypatch)
    assert _close().exit_code == 0
    plain = store.get_session(sessions["proj"].id)["final_summary"]

    monkeypatch.setattr("phasectl.cli.api_compress", lambda *a, **k: "MODEL SUMMARY")
    config["defaults"]["project"] = "other"
    result = _close("--summarize", "--quiet")

    assert result.exit_code == 0, result.output
    assert WARNING in result.stderr
    summarized = store.get_session(sessions["other"].id)["final_summary"]
    expected_plain = plain.replace(sessions["proj"].id[:8], sessions["other"].id[:8])
    assert summarized == f"{expected_plain}\n\n{MODEL_LABEL}\nMODEL SUMMARY"


def test_inv_001_08_graph_from_deterministic_part(env, monkeypatch):
    store, _, graph_calls = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "user", "Real decision DECISION-001")
    monkeypatch.setattr(
        "phasectl.cli.api_compress",
        lambda *a, **k: "Model invents RFC-777 and DECISION-778",
    )

    assert _close("--summarize").exit_code == 0
    summary = store.get_session(session.id)["final_summary"]
    deterministic = summary.split(f"\n\n{MODEL_LABEL}")[0]
    assert graph_calls == [deterministic]
    assert "DECISION-778" not in graph_calls[0]
    assert "RFC-777" not in graph_calls[0]


def test_inv_001_09_summarize_failure_keeps_session_open(env, monkeypatch):
    store, _, graph_calls = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "user", "DECISION-001")

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("phasectl.cli.api_compress", boom)

    result = _close("--summarize")

    assert result.exit_code == 2
    assert "Compression failed: provider down" in result.stderr
    assert store.get_active_session("proj")["id"] == session.id
    assert store.get_session(session.id)["final_summary"] is None
    assert graph_calls == []


def test_inv_001_10_no_active_session(env):
    result = _close()
    assert result.exit_code == 3
    assert "No active session to close." in result.stderr


def test_inv_001_11_stdout_line(env, monkeypatch):
    store, _, _ = env
    session = open_session(store, "proj", "design")
    _add(store, session.id, "user", "one")
    _add(store, session.id, "assistant", "two")
    _add(store, session.id, "system", "PRIOR_DECISIONS: unavailable", origin="prior_decisions")
    _add(store, session.id, "system", "[COMPRESSED SUMMARY: x]", origin="compression", phase="_compression")
    _forbid_models(monkeypatch)

    result = _close()

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    match = re.fullmatch(r"([0-9a-f]{8}) (\d+)→1 (\d+)t", lines[0])
    assert match and match.group(1) == session.id[:8] and match.group(2) == "3"
