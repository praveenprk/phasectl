# VIOLATIONS.md
# phasectl — Violations Register
# Append only. No violation is ever removed. A violation may be marked
# RESOLVED with date and commit reference.
#
# Severity (eng-principles ENG-STD-006 §2.3):
#   CRITICAL — a MUST is broken now · HIGH — a MUST is degraded
#   MEDIUM — a SHOULD is broken · LOW — debt
# Severities of V-ENG-002-01..006-01 confirmed by the governor (PK), 2026-11-08.
#
# IDs: V-ENG-<standard>-<NN> = violation of an eng-principles standard.
#      V-<SPEC>-<NN>         = violation of a phasectl SPEC (none exist yet).

## Summary (2026-11-08)

| ID | Standard | Severity | Status |
|----|----------|----------|--------|
| V-ENG-002-01 | ENG-STD-002 §1.1 | HIGH | OPEN |
| V-ENG-004-01 | ENG-STD-004 §4.1 | HIGH | OPEN |
| V-ENG-004-02 | ENG-STD-004 §6.1 | HIGH | OPEN |
| V-ENG-005-01 | ENG-STD-005 §1, §2.2 | HIGH | RESOLVED |
| V-ENG-006-01 | ENG-STD-006 §1 | HIGH | RESOLVED |
| V-ENG-005-02 | ENG-STD-005 §1.3 | HIGH (proposed) | OPEN |
| V-ENG-002-02 | ENG-STD-002 §1.1, §4.1 | CRITICAL (proposed) | OPEN |

---

### V-ENG-002-01
SPEC says : ENG-STD-002 §1.1 — "All work passes through five gates, in order." A SPEC precedes TEST and BUILD.
Code does : No RFC or SPEC exists for any shipped behaviour. Design lives in docs/internal/DESIGN.md and ARCHITECTURE.md, which are not gated specs with falsifiable invariants.
Severity  : HIGH
Impact    : Tests (tests/test_*_v0.py) have no SPEC invariants to trace to. A behaviour change cannot be checked against a contract.
Fix       : Backfill (ENG-STD-002 §4.1): write an RFC and SPEC per command surface, honestly describing current behaviour. Start with `close` (session snapshot + graph), then `start`/resume. Then map existing tests to INV ids.
When      : Before the next feature.
Status    : OPEN
Anchor    : DECISION-001

### V-ENG-004-01
SPEC says : ENG-STD-004 §4.1 — "Errors MUST NOT be silently swallowed." ENG-P-08 — fail closed.
Code does : src/phasectl/api.py:108-112 `_get_graph_store` catches every `Exception` and returns None. `persist_session_graph` then returns silently. `phasectl close` reports success while the graph was never updated.
Severity  : HIGH
Impact    : The session graph silently diverges from SQLite. `phasectl graph` under-reports linked RFCs and DECISIONs without any signal.
Fix       : Per docs/internal/DECISION-CONTINUITY-FOLLOWUPS.md: define failure reporting and reconciliation before changing persistence order. A minimum first step is to report the failure on stderr with a non-zero exit, or a warning, and leave SQLite as the source record.
When      : With the `close` SPEC (V-ENG-002-01).
Status    : OPEN
Anchor    : DECISION-001

### V-ENG-004-02
SPEC says : ENG-STD-004 §6.1 — "Each project MUST pin one formatter and one linter per language and run them in CI."
Code does : No formatter or linter is configured in pyproject.toml. There is no CI (.github/ absent), so tests and checks run only by hand.
Severity  : HIGH
Impact    : Style and test regressions are caught only if someone remembers to run them.
Fix       : Pin a formatter and linter (dev dependency + config). Add CI running them, `python -m pytest`, and `scripts/hooks/test`.
When      : Next chore change.
Status    : OPEN
Anchor    : DECISION-001

### V-ENG-005-01
SPEC says : ENG-STD-005 §1.3 — every commit MUST carry a resolving anchor. §2.2 — the default branch is protected.
Code does : Commits through cbdcfda are free-form (e.g. c2a4749 "fix: retain decision references in snapshots"). No hooks were installed.
Severity  : HIGH
Impact    : Pre-adoption history has no machine-traceable reasons.
Fix       : Vendor eng-principles hooks into scripts/hooks/ with phasectl's anchors policy. Enable host branch protection on main. History is not rewritten (append-only).
When      : This change.
Status    : RESOLVED — 2026-11-08, adoption change on branch chore/DECISION-001-adopt-eng-principles (forward only; earlier commits remain unanchored)
Anchor    : DECISION-001

### V-ENG-006-01
SPEC says : ENG-STD-006 §1 — every project MUST keep DECISIONS, VIOLATIONS, and an ICS.
Code does : None existed. One follow-up note lived in docs/internal/DECISION-CONTINUITY-FOLLOWUPS.md.
Severity  : HIGH
Impact    : Decisions and known defects are not discoverable from one place.
Fix       : Create docs/DECISIONS.md (with backfilled DECISION-002..005), this register, and docs/conformance/phasectl-ICS.md.
When      : This change.
Status    : RESOLVED — 2026-11-08, adoption change on branch chore/DECISION-001-adopt-eng-principles
Anchor    : DECISION-001

### V-ENG-005-02
SPEC says : ENG-STD-005 §1.3 — "Every commit MUST carry at least one anchor that resolves to a governed entry."
Code does : b3a2376 "fix: carry prior decisions into new sessions deterministically" (PR #11) landed on main at 2026-11-08T13:30Z, after adoption (PR #12, 13:18Z), with no anchor. It was written before adoption. GitHub's merge creates the commit server-side, so the local commit-msg hook never ran. Nothing on the host checks commit subjects.
Severity  : HIGH (proposed; governor to confirm)
Impact    : One post-adoption commit on main has no machine-traceable reason. The same path is open for every PR: host merges bypass commit-msg.
Fix       : History is not rewritten. This entry is b3a2376's anchor. Close the path: CI runs scripts/hooks/commit-msg on every PR commit subject and on the PR title used for squash merges (with V-ENG-004-02).
When      : With the CI change for V-ENG-004-02.
Status    : OPEN
Anchor    : DECISION-001

### V-ENG-002-02
SPEC says : ENG-STD-002 §1.1 — "All work passes through five gates, in order." §4.1 — work that bypassed a gate opens a CRITICAL violation and gets an honest backfilled RFC/SPEC.
Code does : PR #11 (b3a2376) added new behaviour to `start` (a CLI-built PRIOR_DECISIONS turn, origin `prior_decisions`, src/phasectl/context.py `build_prior_decisions_block`) without an RFC or SPEC. Its evidence was an internal experiment and tests/test_prior_decisions_v0.py, not SPEC invariants.
Severity  : CRITICAL (per §4.1; proposed, governor to confirm)
Impact    : The PRIOR_DECISIONS guarantees (verbatim, labelled historical, no model call, explicit when empty) are tested but not specified. They can change without a contract to check against.
Fix       : Backfill: the `start` SPEC (V-ENG-002-01 order: close, then start) states the PRIOR_DECISIONS behaviour as invariants, and the existing tests map to them.
When      : With the close/start SPECs (V-ENG-002-01).
Status    : OPEN
Anchor    : DECISION-001
