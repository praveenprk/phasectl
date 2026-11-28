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
| V-ENG-004-02 | ENG-STD-004 §6.1 | HIGH | RESOLVED |
| V-ENG-005-01 | ENG-STD-005 §1, §2.2 | HIGH | RESOLVED |
| V-ENG-006-01 | ENG-STD-006 §1 | HIGH | RESOLVED |
| V-ENG-005-02 | ENG-STD-005 §1.3 | HIGH (proposed) | OPEN |
| V-ENG-002-02 | ENG-STD-002 §1.1, §4.1 | CRITICAL (proposed) | OPEN |
| V-ENG-006-02 | ENG-STD-006 §2.4 | HIGH (proposed) | RESOLVED |
| V-ENG-004-03 | ENG-STD-004 §0, §6.1 | HIGH | RESOLVED |
| V-ENG-008-01 | ENG-STD-008 security | HIGH | RESOLVED |
| V-ENG-008-06 | ENG-STD-008 changelog | HIGH | RESOLVED |

---

### V-ENG-002-01
SPEC says : ENG-STD-002 §1.1 — "All work passes through five gates, in order." A SPEC precedes TEST and BUILD.
Code does : No RFC or SPEC exists for any shipped behaviour. Design lived in docs/internal/DESIGN.md and ARCHITECTURE.md (since removed), which are not gated specs with falsifiable invariants.
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
Fix       : `close` commits to SQLite before the graph write and the two share no transaction, so a graph failure after the commit leaves the graph partial, and re-running `close` cannot recover a closed session. Reversing the order only inverts the failure window, and a killed process can leave either partial state. So define failure reporting and a retry, reconciliation or compensation strategy before changing persistence order. The original SQLite turns stay the source record for rebuilding the graph. A minimum first step is to report the failure on stderr with a non-zero exit, or a warning, and leave SQLite as the source record.
When      : With the `close` SPEC (V-ENG-002-01).
Done      : e13143a: close warns on stderr when the graph update fails (INV-001-12, Q-003). Reconciliation of a partial graph is still open.
Status    : OPEN
Anchor    : DECISION-001

### V-ENG-004-02
SPEC says : ENG-STD-004 §6.1 — "Each project MUST pin one formatter and one linter per language and run them in CI."
Code does : No formatter or linter is configured in pyproject.toml. There is no CI (.github/ absent), so tests and checks run only by hand.
Severity  : HIGH
Impact    : Style and test regressions are caught only if someone remembers to run them.
Fix       : ruff 0.17.5 pinned as the formatter and linter (pyproject.toml, explicit rule set E4, E7, E9, F, I). .github/workflows/ci.yml runs `ruff check`, `ruff format --check`, `python -m pytest` on Python 3.11 and 3.13, and `scripts/hooks/test`. The ruleset `main-branch-integrity` requires all of them.
When      : 2026-11-16
Status    : RESOLVED
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
Code does : None existed. One follow-up note lived in docs/internal/DECISION-CONTINUITY-FOLLOWUPS.md (since folded into V-ENG-004-01).
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
Fix       : History is not rewritten. This entry is b3a2376's anchor. Path closed: .github/workflows/commit-check.yml calls eng-principles' shared check (ENG-DEC-009, ENG-STD-005 §1.5) on the PR title, description and every commit, and the ruleset `main-branch-integrity` requires `commit-check / check`.
When      : 2026-11-16
Status    : RESOLVED (forward only)
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


### V-ENG-006-02
SPEC says : ENG-STD-006 §2.4 (refined by ENG-DEC-015 Q-001) — a project ICS MAY use `not assessed`; "Each such row MUST state why it is not assessed and what assessing it needs."
Code does : docs/conformance/phasectl-ICS.md marks ENG-STD-001 and ENG-STD-007 `not assessed` with no reason, and the ENG-STD-003 row leaves §0.5 and §0.6 "not yet assessed" with no reason.
Severity  : HIGH (proposed; governor to confirm)
Impact    : A reader cannot tell an omission from a deliberate deferral, or what work would close it.
Fix       : Give each `not assessed` row and clause its reason and what assessing it needs.
When      : 2026-11-23
Status    : RESOLVED — each `not assessed` row and clause in docs/conformance/phasectl-ICS.md states why and what assessing it needs. The ENG-STD-002 row, stale at `not yet`, is corrected to `partial` with its evidence

### V-ENG-004-03
SPEC says : ENG-STD-004 §0.1 — code is any executable text, scripts included; §6.1 — "Each project MUST pin one formatter and one linter per language and run them in CI." ENG-DEC-015 Q-003: fmt_lint covers every language found in the repository.
Code does : phasectl writes shell (scripts/field-test.sh, bash). CI pins ruff for Python only; no shell formatter or linter is pinned or run. shellcheck 0.11.0 reports SC1007 on scripts/field-test.sh:4 (`CDPATH= cd`), and shfmt 3.14.1 reports formatting drift.
Severity  : HIGH
Impact    : Shell defects and style drift in the field-test runner are caught only by hand.
Fix       : Pin shellcheck and shfmt (dev extras), run both on phasectl's own shell in CI, fix the findings. scripts/hooks/ is vendored from eng-principles 74e7e76 and is not edited here: its lint and format belong upstream (shellcheck 0.11.0 reports SC2034 at scripts/hooks/pre-push:32).
When      : 2026-11-23
Status    : RESOLVED — shellcheck 0.11.0.1 (shellcheck-py) and shfmt 3.14.1 (shfmt-py 4.2.0) pinned in pyproject.toml; .github/workflows/ci.yml job `shell-lint` runs both on scripts/field-test.sh; SC1007 fixed (`CDPATH=''`) and the file formatted (shfmt -i 4 -ci -sr, eng-principles' flags). SC2034 in the vendored pre-push is reported upstream and fixed in eng-principles PR #21 (open); re-vendoring after it merges is a separate phasectl change. Making `shell-lint` a required check is a ruleset change for the governor (ENG-STD-005 §2.2); until then CI runs it on every PR without blocking merge
Anchor    : DECISION-001

### V-ENG-008-01
SPEC says : ENG-STD-008 check `security` (MUST; OpenSSF Best Practices vulnerability_report_process) — a SECURITY.md with a private way to report a vulnerability.
Code does : No SECURITY.md. Private vulnerability reporting is enabled on the repository (`gh api repos/praveenprk/phasectl/private-vulnerability-reporting`: enabled, 2026-11-25), but nothing tells a reporter to use it.
Severity  : HIGH (audit: security floor; Scorecard Security-Policy)
Impact    : A reporter may open a public issue and disclose a vulnerability before a fix.
Fix       : SECURITY.md: private vulnerability reporting first, the maintainer's email as fallback (as factory-protocol), scope and response time.
When      : 2026-11-25
Status    : RESOLVED — SECURITY.md added in this change
Anchor    : DECISION-001

### V-ENG-008-06
SPEC says : ENG-STD-008 check `changelog` (MUST; Keep a Changelog 1.1.0, OpenSSF Best Practices release_notes) — a CHANGELOG.
Code does : No CHANGELOG.md. Changes since v0.1.0 are only in commit and pull request history.
Severity  : HIGH (audit: OpenSSF BP MUST release_notes)
Impact    : Users cannot see what changed between releases without reading git history.
Fix       : CHANGELOG.md in Keep a Changelog format, backfilled from v0.1.0 (2026-08-02) and merged pull requests #2–#24.
When      : 2026-11-26
Status    : RESOLVED — CHANGELOG.md added in this change
Anchor    : DECISION-001
