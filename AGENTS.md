# AGENTS.md — phasectl

Instructions for every automated contributor, whatever its model or tool.
Tool-specific files (CLAUDE.md and the like) only point here
(eng-principles ENG-STD-005 §6).

phasectl follows eng-principles (docs/DECISIONS.md DECISION-001).

- Before work: read docs/DECISIONS.md and docs/VIOLATIONS.md. The decision or defect may already be recorded.
- Every change traces to an anchor: DECISION-NNN, V-…, RFC-NNN, SPEC-NNN, or INV-NNN-NN. No anchor means no work.
- Install the hooks: `scripts/hooks/install`. Never bypass them (`--no-verify` is a violation).
- Branch `type/ANCHOR-description`. Commit `type(scope): description — ANCHOR`. No co-author or AI attribution lines.
- A violation fix updates docs/VIOLATIONS.md in the same commit.
- Do not mark anything `conformant` in the ICS without a cited test or commit.
- Codebase rule (DECISION-002): phasectl never writes to the user's codebase. Do not add code that does.
- Only the maintainer pushes to `main` and merges.
