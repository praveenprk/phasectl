"""Regression tests for fabricated tool use in tool-less phases (#6)."""

import pytest
from typer.testing import CliRunner

from phasectl.api import chat as api_chat
from phasectl.cli import app
from phasectl.phases import load_phase
from phasectl.session import open_session
from phasectl.store import SQLiteStore

GUARDRAIL = (
    "You have no tools in this phase. Do not write command output, file contents, "
    "tool calls, or anything that looks like tool use; say what you would check instead."
)


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    store = SQLiteStore(str(db_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    session = open_session(store, "proj", "orient")
    config = {
        "defaults": {"project": "proj"},
        "storage": {"db_path": str(db_path)},
        "projects": {"proj": {"index_path": str(repo)}},
    }
    monkeypatch.setattr("phasectl.cli._load_config", lambda: config)
    monkeypatch.setattr("phasectl.cli._get_store", lambda config: store)
    monkeypatch.setattr("phasectl.cli._resolve_project", lambda project, config: "proj")
    monkeypatch.setattr("phasectl.cli.get_git_state", lambda path: {})
    monkeypatch.setattr(
        "phasectl.cli.persist_session_graph", lambda *args, **kwargs: None
    )
    return store, session


def _chat_reply(monkeypatch, response, tools_available=0):
    monkeypatch.setattr(
        "phasectl.cli.api_chat", lambda **kwargs: (response, tools_available)
    )
    result = CliRunner().invoke(app, ["chat", "Review the current state"])
    assert result.exit_code == 0, result.output


def _close_input(monkeypatch):
    captured = {}

    def fake_compress(turns_text, config, system_prompt=None):
        captured["turns_text"] = turns_text
        return "model summary"

    monkeypatch.setattr("phasectl.cli.api_compress", fake_compress)
    result = CliRunner().invoke(app, ["close", "--quiet", "--summarize"])
    assert result.exit_code == 0, result.output
    return captured["turns_text"]


def test_toolless_phase_prompt_contains_guardrail(monkeypatch):
    captured = {}

    class FakeProvider:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return "I would check the current state.", []

    monkeypatch.setattr("phasectl.api.get_provider", lambda config: FakeProvider())

    _, tools_available = api_chat(
        messages=[], phase_config=load_phase("orient"), app_config={}
    )

    assert tools_available == 0
    assert captured["tools"] is None
    assert GUARDRAIL in captured["system"]


@pytest.mark.parametrize(
    "marker", ["<tool_call>", "<tool_response>", "<function_calls>", "<invoke"]
)
def test_flagged_turn_is_excluded_from_close_input(session_env, monkeypatch, marker):
    store, session = session_env
    response = f"{marker}fabricated command output"
    _chat_reply(monkeypatch, response)

    assert store.get_turns(session.id)[-1]["origin"] == "assistant_unverified_tool_use"
    turns_text = _close_input(monkeypatch)

    assert response not in turns_text
    assert "1 turns were excluded as unverified." in turns_text.splitlines()
    assert response not in store.get_session(session.id)["final_summary"]


def test_normal_turn_is_included_in_close_input(session_env, monkeypatch):
    store, session = session_env
    response = "I would check the current state before making any claims."
    _chat_reply(monkeypatch, response)

    assert store.get_turns(session.id)[-1]["origin"] == "assistant"
    turns_text = _close_input(monkeypatch)

    assert response in turns_text
    assert "excluded as unverified" not in turns_text
