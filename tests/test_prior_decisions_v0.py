"""Prior Decisions v0 — decisions survive the session handoff deterministically.

Experiment 002 (phasectl-exp-001/RESULTS.md) showed `close` preserves
DECISION references in the snapshot, but the default resume path routes them
through the orient synthesis (a model rewrite) and hides the prior-session
turn, so session 2 lost the decisions in 5/6 trials.

Contract:
- `start` stores a CLI-owned PRIOR_DECISIONS turn built from the last closed
  snapshot: only the lines that carry decision references, verbatim.
- It is explicit when there is nothing to carry (unavailable / none recorded)
  so the model has no gap to fill.
- `chat` sends it by default, while still excluding the rest of the prior
  session (Context Boundary v0 is unchanged).
- It is never a model rewrite: no compression call is involved.
"""

from typer.testing import CliRunner

from phasectl.cli import app
from phasectl.context import build_prior_decisions_block
from phasectl.session import open_session
from phasectl.store import SQLiteStore


SUMMARY = (
    "**Decisions:**\n\n"
    "* Use SQLite for storage (DECISION-001)\n"
    "* Store amounts as integer cents (DECISION-002)\n\n"
    "Some unrelated prose that must stay out: PRIOR-SESSION-PROSE.\n"
    "```\nDECISION-999 inside a code fence\n```\n"
)


def test_block_carries_verbatim_decision_lines_only():
    block = build_prior_decisions_block({"id": "abcdef0123", "final_summary": SUMMARY})

    assert block.startswith("PRIOR_DECISIONS")
    assert "abcdef01" in block
    assert "- Use SQLite for storage (DECISION-001)" in block
    assert "- Store amounts as integer cents (DECISION-002)" in block
    assert "PRIOR-SESSION-PROSE" not in block
    assert "DECISION-999" not in block
    assert "not verified" in block


def test_block_deduplicates_lines_and_keeps_first_occurrence_order():
    summary = "DECISION-002 and DECISION-001 together.\nLater: DECISION-001 again.\n"
    block = build_prior_decisions_block({"id": "s1", "final_summary": summary})

    lines = [l for l in block.splitlines() if l.startswith("- ")]
    assert lines == ["- DECISION-002 and DECISION-001 together."]


def test_block_is_explicit_when_nothing_to_carry():
    assert build_prior_decisions_block(None) == (
        "PRIOR_DECISIONS: unavailable (no closed session for this project)"
    )
    assert build_prior_decisions_block({"id": "s1", "final_summary": "no refs here"}) == (
        "PRIOR_DECISIONS: none recorded in last session snapshot (s1)"
    )


def _config(tmp_path, db_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return repo, {
        "defaults": {"project": "proj"},
        "storage": {"db_path": str(db_path)},
        "projects": {"proj": {"index_path": str(repo)}},
    }


def _close_prior(store, summary):
    prior = open_session(store, "proj", "design")
    store.close_session(prior.id, "design", summary, 10)
    return prior


def test_start_stores_and_prints_prior_decisions(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    _close_prior(store, SUMMARY)
    repo, config = _config(tmp_path, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)

    def no_model(*args, **kwargs):
        raise AssertionError("PRIOR_DECISIONS must not require a model call")

    monkeypatch.setattr("phasectl.cli.api_compress", no_model)

    result = CliRunner().invoke(app, ["start", "--phase", "design"])

    assert result.exit_code == 0, result.output
    assert "- Use SQLite for storage (DECISION-001)" in result.output
    active = store.get_active_session("proj")
    turns = store.get_turns(active["id"])
    stored = [t for t in turns if t["origin"] == "prior_decisions"]
    assert len(stored) == 1
    assert "DECISION-002" in stored[0]["content"]


def test_start_without_history_stores_explicit_unavailable(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    repo, config = _config(tmp_path, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)

    result = CliRunner().invoke(app, ["start", "--phase", "design"])

    assert result.exit_code == 0, result.output
    turns = store.get_turns(store.get_active_session("proj")["id"])
    assert [t["content"] for t in turns if t["origin"] == "prior_decisions"] == [
        "PRIOR_DECISIONS: unavailable (no closed session for this project)"
    ]


def test_chat_sends_prior_decisions_but_not_prior_session_prose(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    _close_prior(store, SUMMARY)
    repo, config = _config(tmp_path, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})

    captured = []

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return "assistant reply", 0

    monkeypatch.setattr("phasectl.cli.api_chat", fake_chat)

    runner = CliRunner()
    assert runner.invoke(app, ["start", "--phase", "design"]).exit_code == 0
    result = runner.invoke(app, ["chat", "what did we decide?"])

    assert result.exit_code == 0, result.output
    contents = [str(m.get("content", "")) for m in captured[0]]
    assert any("Use SQLite for storage (DECISION-001)" in c for c in contents)
    assert any("Store amounts as integer cents (DECISION-002)" in c for c in contents)
    assert not any("PRIOR-SESSION-PROSE" in c for c in contents)


def test_close_carries_decisions_into_next_snapshot(tmp_path, monkeypatch):
    """Chained handoff: session 2 closes without the model restating decisions."""
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    _close_prior(store, SUMMARY)
    repo, config = _config(tmp_path, db_path)
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli.api_compress", lambda *a, **k: "model summary with no refs")

    runner = CliRunner()
    assert runner.invoke(app, ["start", "--phase", "design"]).exit_code == 0
    result = runner.invoke(app, ["close"])

    assert result.exit_code == 0, result.output
    last = store.get_last_session("proj")
    assert "DECISION-001" in last["final_summary"]
    assert "DECISION-002" in last["final_summary"]
