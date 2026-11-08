# SPEC-002 — `start`: session handoff context
RFC: RFC-001 · Status: DRAFT · Quality characteristics constrained (ENG-STD-007): functional suitability, reliability

## Contract (ENG-STD-003 §6)
Accepts    : `phasectl start [--project P] [--phase PH] [--quiet] [--no-fuse] [--seed FILE]… [--summarize]`.
Guarantees : A new session is opened, and the handoff turns below are stored in it, with no model call by default. Stdout carries the human-readable handoff, and its last line is `session opened: <session-id-8> <phase>`. Diagnostics go to stderr; `--quiet` suppresses progress lines but never warnings.
Refuses    : A session is already active for the project → stderr `Session already active for project '<P>'. Close it first.`, exit 1, nothing changed. Unknown phase → stderr `Phase '<PH>' not found. Available phases: …`, exit 1, nothing changed. Missing, unsupported or empty `--seed` file → stderr `[seed] …` line; that file is skipped and `start` continues.

Source turns and decision lines are as defined in SPEC-001. The **prior session** is the most recently closed session of the project.

## Stored handoff turns (role `system`, in this order)

| Origin | When | Content |
|--------|------|---------|
| `prior_session` | prior session exists | Unchanged from current behaviour: the prior snapshot and its last uncompressed turns. Excluded from `chat` by default (SPEC-003). |
| `prior_decisions` | always | `PRIOR_DECISIONS (historical record, verbatim from session <id-8> turns; not verified against the current repository):` followed by `- <decision line>` for each decision line of the prior session's source turns. Lines from conversation turns are tagged `[<role>]`, and carried lines are copied as they stand. |
| `prior_turns` | always | `PRIOR_LAST_TURNS (historical, verbatim from session <id-8>; not verified against the current repository):` followed by the prior session's last two conversation turns as `[<role>]: <content>`. |
| `agent_context` | phase `orient`, no `--no-fuse`, transcript found | `AGENT_CONTEXT (external, verbatim excerpt of <path>: last messages, each cut to 1200 chars, at most 4000 chars; not verified):` followed by the excerpt. |
| `seed` | each usable `--seed` file | `SEED <basename> (supplied, verbatim; not verified):` followed by the file text. If the file exceeds 200 KB, the header adds `; TRUNCATED to first 200KB`. |
| `resume` | `--summarize`, phase `orient`, no `--no-fuse`, model call succeeds | The model resume synthesis (existing `resume.build_resume_block`). |
| `seed_gist` | `--summarize`, per usable seed file, model call succeeds | The model gist of that file. |

Explicit empty states: no prior session → `PRIOR_DECISIONS: unavailable (no closed session for this project)` and `PRIOR_LAST_TURNS: unavailable (no closed session for this project)`. Prior session without decision lines → `PRIOR_DECISIONS: none recorded in session <id-8>`. Prior session without conversation turns → `PRIOR_LAST_TURNS: none recorded in session <id-8>`.

## Stdout, in order
1. Phase `orient` without `--no-fuse`: `CURRENT_GIT_STATE: …` as today (DECISION-005).
2. The `prior_decisions` content.
3. The `prior_turns` content.
4. If an `agent_context` turn was stored: `AGENT_CONTEXT: <n> chars from <path>` (one line; the excerpt itself is not printed).
5. With a `resume` turn: `[MODEL-DERIVED RESUME (non-authoritative)]` and the synthesis.
6. `session opened: <session-id-8> <phase>`.

## Invariants
INV-002-01 — `start` without `--summarize` MUST NOT call any model, with or without `--seed`. Verified by: tests/test_spec002_start.py::test_inv_002_01_no_model_call_by_default
INV-002-02 — Every successful `start` MUST store exactly one `prior_decisions` turn and print its content. Its lines MUST be the prior session's decision lines (SPEC-001 rules: verbatim, untruncated, first-occurrence order, deduplicated), or the explicit empty state. Verified by: test_inv_002_02_prior_decisions_from_source_turns
INV-002-03 — `prior_decisions` MUST NOT be derived from the prior session's `final_summary`. Verified by: test_inv_002_03_prior_decisions_ignore_model_summary
INV-002-04 — Decisions MUST carry across chained sessions: a decision line from session 1 appears in session 3's `prior_decisions` even if session 2 never mentions it. Verified by: test_inv_002_04_decisions_chain_across_sessions
INV-002-05 — Every successful `start` MUST store exactly one `prior_turns` turn holding the prior session's last two conversation turns verbatim, or the explicit empty state. Verified by: test_inv_002_05_prior_turns_verbatim
INV-002-06 — In phase `orient` without `--no-fuse`, a found coding-agent transcript MUST be stored as one `agent_context` turn whose header states the source path and the caps applied. With `--no-fuse` or another phase, none is stored. Verified by: test_inv_002_06_agent_context_verbatim_excerpt
INV-002-07 — Each usable `--seed` file MUST be stored verbatim as a `seed` turn. A file over 200 KB MUST be marked truncated in the stored text. Verified by: test_inv_002_07_seed_verbatim
INV-002-08 — With `--summarize`, each model step MUST print `warning: <step> may cause signal loss due to compression` on stderr (`<step>` is `resume synthesis` or `seed gist`, even under `--quiet`). Model output MUST be stored only as `resume` / `seed_gist` turns in addition to the turns of INV-002-02..07, which MUST be unchanged. Verified by: test_inv_002_08_summarize_adds_labelled_turns_and_warns
INV-002-09 — With `--summarize`, a failed model step MUST NOT stop `start`. The step's failure is reported on stderr and no turn is stored for it. Verified by: test_inv_002_09_summarize_failure_is_reported_not_stored
INV-002-10 — `CURRENT_GIT_STATE` MUST come from live git and print `unavailable` for any field git cannot supply, never a substitute value (DECISION-005, backfill). Verified by: tests/test_context_boundary_v0.py::test_start_unavailable_working_tree_status_displays_unavailable, test_start_preserves_git_state_when_resume_synthesis_fails
INV-002-11 — The refusals in the contract MUST exit 1 and change nothing. Verified by: test_inv_002_11_refusals

## Out of scope
- Legacy sessions closed before SPEC-001: their stored turns are used as they are. A legacy `prior_decisions` turn uses the PR #11 format, and its lines are carried as they stand.
- How a model reads or labels these turns (Experiment 002: small models can mislabel provenance).
- The provider's context window (SPEC-003 out of scope).
