# phasectl — Implementation Conformance Statement (ICS)

This is the only place for phasectl's conformance status. Normative documents
state none (eng-principles ENG-STD-006). Vocabulary:
`conformant` / `partial` / `not yet` / `n/a`. No `conformant` without evidence.

Date of last update: 2026-11-08
Baseline: origin/main cbdcfda. `python -m pytest`: 34 passed (Python 3.14, 2026-11-08).

## phasectl SPECs

No SPECs exist yet (V-ENG-002-01). Per-invariant rows start here once
SPEC-001 is closed.

## eng-principles standards

| Standard | Status | Evidence | Open violations |
|----------|--------|----------|-----------------|
| ENG-STD-001 Language & IDs | not assessed | — | — |
| ENG-STD-002 Lifecycle gates | not yet | No RFC/SPEC | V-ENG-002-01 |
| ENG-STD-003 Design | partial | §0.3: README "progress to stderr … stdout stays clean for piping". §5 D: `LLMProvider` ABC (DECISION-004). §0.2/0.8: indexer is a plain binary contract (DECISION-003). §0.5 exit codes and §0.6 minimum substrate not yet assessed | — |
| ENG-STD-004 Construction | partial | §3.1: CONTRIBUTING "why-only comments". §7.1: "no new dependencies without a strong reason" | V-ENG-004-01, V-ENG-004-02 |
| ENG-STD-005 Change control | partial | scripts/hooks/ (commit-msg, pre-push). scripts/hooks/test passes 20/20. AGENTS.md. Host branch protection on main: pending | — |
| ENG-STD-006 Registers | conformant | docs/DECISIONS.md, docs/VIOLATIONS.md, this file | — |
| ENG-STD-007 Quality model | not assessed | — | — |
