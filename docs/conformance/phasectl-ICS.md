# phasectl — Implementation Conformance Statement (ICS)

This is the only place for phasectl's conformance status. Normative documents
state none (eng-principles ENG-STD-006). Vocabulary:
`conformant` / `partial` / `not yet` / `n/a`. No `conformant` without evidence.

Date of last update: 2026-11-16
Baseline: origin/main 1639f2a. `python -m pytest`: 82 passed (Python 3.14, 2026-11-16).

## phasectl SPECs

SPEC-001 (`close`), SPEC-002 (`start`), SPEC-003 (`chat`) are DRAFT (RFC-001,
DECISION-006). Per-invariant rows start here once they are closed.

## eng-principles standards

| Standard | Status | Evidence | Open violations |
|----------|--------|----------|-----------------|
| ENG-STD-001 Language & IDs | not assessed | — | — |
| ENG-STD-002 Lifecycle gates | not yet | No RFC/SPEC | V-ENG-002-01, V-ENG-002-02 |
| ENG-STD-003 Design | partial | §0.3: README "progress to stderr … stdout stays clean for piping". §5 D: `LLMProvider` ABC (DECISION-004). §0.2/0.8: indexer is a plain binary contract (DECISION-003). §0.5 exit codes and §0.6 minimum substrate not yet assessed | — |
| ENG-STD-004 Construction | partial | §3.1: CONTRIBUTING "why-only comments". §7.1: "no new dependencies without a strong reason" | V-ENG-004-01, V-ENG-004-02 |
| ENG-STD-005 Change control | conformant | Ruleset `main-branch-integrity` and branch protection on main: PR required, linear history, no force push or deletion, required check `commit-check / check`, eng-principles' shared check on GitHub (ENG-STD-005 §1.5, ENG-DEC-009). scripts/hooks/ (commit-msg, pre-push, check-pr) vendored at eng-principles 74e7e76; scripts/hooks/test passes. AGENTS.md. b3a2376 stays unanchored (V-ENG-005-02, resolved forward only) | — |
| ENG-STD-006 Registers | conformant | docs/DECISIONS.md, docs/VIOLATIONS.md, this file | — |
| ENG-STD-007 Quality model | not assessed | — | — |
