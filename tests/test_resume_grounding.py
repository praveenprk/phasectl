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

    result = build_resume_block(git, session_summary, agent_context, {})

    assert f"branch: {LIVE_BRANCH}" in result
    assert f"branch: {HIST_BRANCH}" not in result


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The public resume block is a single prose string: it cannot mark which "
        "branch claim is authoritative current Git state versus synthesized "
        "historical narrative, so a consumer can still mistake "
        f"{HIST_BRANCH} for current state."
    ),
)
def test_resume_block_exposes_authoritative_current_git_state_boundary(monkeypatch):
    git = {"branch": LIVE_BRANCH, "dirty": False, "dirty_count": 0}
    session_summary = f"Prior session summary references {HIST_BRANCH}."
    agent_context = f"Agent transcript references {HIST_BRANCH}."

    monkeypatch.setattr(
        "phasectl.resume.compress",
        lambda *a, **k: (
            "Resume here: currently on "
            f"{HIST_BRANCH}\nState: branch={HIST_BRANCH} (current)"
        ),
    )

    result = build_resume_block(git, session_summary, agent_context, {})

    # Required property: an unambiguous, machine-identifiable authoritative
    # current-state field must be distinguishable from synthesized narrative.
    assert "AUTHORITATIVE_CURRENT_GIT_STATE: branch=" + LIVE_BRANCH in result
    assert HIST_BRANCH not in result.split("AUTHORITATIVE_CURRENT_GIT_STATE", 1)[-1]
