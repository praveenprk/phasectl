# SPEC-001 — `close`: deterministic session snapshot
RFC: RFC-001 · Status: DRAFT · Quality characteristics constrained (ENG-STD-007): functional suitability, reliability

## Contract (ENG-STD-003 §6)
Accepts    : `phasectl close [--quiet] [--summarize]`. One active session for the resolved project.
Guarantees : The active session is closed with a snapshot (`sessions.final_summary`) built only from its stored turns, with no model call by default. Stdout is one line: `<session-id-8> <turns>→1 <tokens>t`.
Refuses    : No active session → stderr `No active session to close.`, exit 3, nothing changed. `--summarize` and the model call fails → stderr `Compression failed: <error>`, exit 2, session stays active, nothing stored. Auth failure under `--summarize` → the auth exit code (1 or 2), session stays active.

## Definitions
- **Conversation turn**: a stored turn with origin `user` or `assistant`, or a legacy turn with no origin and role `user` or `assistant`.
- **Carried turn**: a stored turn with origin `prior_decisions`.
- **Source turns**: conversation turns plus carried turns, in stored order, whatever their `compressed` flag. Turns with phase `_compression` and turns with any other origin (`prior_session`, `prior_turns`, `agent_context`, `resume`, `seed`, `seed_gist`, `compression`) are never source turns.
- **Decision line**: a line of a source turn's visible prose (code spans and fences excluded, as `graph.extract_decision_references`) that contains at least one DECISION reference. It is stripped of surrounding whitespace and one leading bullet marker (`-`, `*`, `•`), and nothing else changes.

## Snapshot format

```
SNAPSHOT <session-id-8> (deterministic, verbatim from <n> stored turns)
DECISION_LINES:
- [<role>] <decision line>
REFERENCES: <ref>, <ref>, …
LAST_TURNS:
[<role>]: <content>
[<role>]: <content>
```

- A decision line from a conversation turn is tagged with that turn's role. A line from a carried turn is copied as it stands there. Its tag came from the session that first recorded it.
- With no decision lines: `DECISION_LINES: none`. With no references: `REFERENCES: none`. With no conversation turns: `LAST_TURNS: none`.
- With `--summarize`, the snapshot above is followed by a blank line, `[MODEL-DERIVED SUMMARY (non-authoritative)]`, and the model output.

## Invariants
INV-001-01 — `close` without `--summarize` MUST NOT call any model. Verified by: tests/test_spec001_close.py::test_inv_001_01_no_model_call_by_default
INV-001-02 — The snapshot MUST contain every distinct decision line of the source turns, verbatim and untruncated, in first-occurrence order, deduplicated by text. Verified by: test_inv_001_02_decision_lines_verbatim_ordered_deduplicated
INV-001-03 — A source turn's `compressed` flag MUST NOT exclude it from INV-001-02, INV-001-04 or INV-001-05. Verified by: test_inv_001_03_compressed_turns_still_sourced
INV-001-04 — `REFERENCES` MUST list every DECISION and RFC reference in the source turns' visible prose, in first-occurrence order, or `none`. Verified by: test_inv_001_04_references_complete
INV-001-05 — `LAST_TURNS` MUST hold the last two conversation turns verbatim, in stored order, or `none`. Verified by: test_inv_001_05_last_turns_verbatim
INV-001-06 — Turns that are not source turns MUST NOT contribute to the snapshot's deterministic part. Verified by: test_inv_001_06_non_source_turns_ignored
INV-001-07 — With `--summarize`, stderr MUST carry `warning: snapshot summary may cause signal loss due to compression`. The deterministic part MUST be byte-identical to the one produced without the flag, and the model output MUST follow it under the model-derived label. Verified by: test_inv_001_07_summarize_appends_labelled_and_warns
INV-001-08 — Graph links (RFC and DECISION nodes) MUST be computed from the deterministic part only. Verified by: test_inv_001_08_graph_from_deterministic_part
INV-001-09 — With `--summarize`, a model failure MUST exit 2 and leave the session active with nothing stored. Verified by: test_inv_001_09_summarize_failure_keeps_session_open
INV-001-10 — No active session MUST exit 3 with `No active session to close.` on stderr. Verified by: test_inv_001_10_no_active_session
INV-001-11 — Stdout MUST be exactly one line, `<session-id-8> <turns>→1 <tokens>t`, where `<turns>` counts stored turns outside phase `_compression`. Verified by: test_inv_001_11_stdout_line

## Out of scope
- Silent graph-store failure on close (V-ENG-004-01).
- What the snapshot is used for at the next `start` (SPEC-002).
- The `compress` command and `loose --synthesize` (already explicit commands; covered by DECISION-006 in a later change).
- Whether a model reports the snapshot's provenance correctly.
