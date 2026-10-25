import pytest

from phasectl.resume import build_resume_block

LIVE_BRANCH = "docs/SPEC-032-caller-scope-binding"
HIST_BRANCH = "feat/RFC-028-scope-resolution-contract"


def test_resume_block_grounds_current_branch_in_live_git(monkeypatch):
    git = {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    session_summary = f"Prior session summary references {HIST_BRANCH}."
    agent_context = f"Agent transcript references {HIST_BRANCH}."

    monkeypatch.setattr(
        "phasectl.resume.compress",
        lambda *a, **k: f"Resume here: on {HIST_BRANCH}\nState: branch={HIST_BRANCH}",
    )

    git_state, synthesis = build_resume_block(git, session_summary, agent_context, {})

    assert git_state.get("branch") == LIVE_BRANCH
    assert HIST_BRANCH in synthesis


def test_resume_block_separates_authoritative_git_state_from_synthesis(monkeypatch):
    git = {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0, "last_commit": {"hash": "abc123", "subject": "s", "when": "now"}}
    session_summary = f"Prior session summary references {HIST_BRANCH}."
    agent_context = f"Agent transcript references {HIST_BRANCH}."

    monkeypatch.setattr(
        "phasectl.resume.compress",
        lambda *a, **k: (
            "Resume here: currently on "
            f"{HIST_BRANCH}\nState: branch={HIST_BRANCH} (current)"
        ),
    )

    git_state, synthesis = build_resume_block(git, session_summary, agent_context, {})

    assert git_state.get("branch") == LIVE_BRANCH
    assert (git_state.get("last_commit") or {}).get("hash") == "abc123"
    assert HIST_BRANCH in synthesis
    assert git_state.get("branch") != HIST_BRANCH
