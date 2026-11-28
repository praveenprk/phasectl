# SPEC-003 — `chat`: context assembly and compression
RFC: RFC-001 · Status: DRAFT · Quality characteristics constrained (ENG-STD-007): functional suitability, reliability

## Contract (ENG-STD-003 §6)
Accepts    : `phasectl chat MESSAGE [--model M] [--quiet] [--prior-session-memory] [--compress]`, with optional stdin. One active session.
Guarantees : One request is assembled from the session's stored turns as specified below and sent; the reply is stored and printed to stdout after the `CURRENT_GIT_STATE` line. No turn is rewritten by a model unless `--compress` is passed.
Refuses    : No active session → stderr `No active session. Run 'phasectl start' first.`, exit 3. Provider or compression error → stderr `API error: …` / `Compression failed: …`, exit 2. Auth failure → the auth exit code (1 or 2).

## Request assembly, in order
1. Every stored turn that is not compressed, in stored order, except `prior_session` turns (and legacy system turns with no origin) unless `--prior-session-memory` is passed. System turns are sent with role `user`, prefixed by their provenance label where one is defined.
2. With `--prior-session-memory` and no stored `prior_session` turn: the prior session memory, labelled historical.
3. The live git block (SPEC-002) observed at invocation.
4. The user message. If stdin is present, it is wrapped as `<context>…</context>` before the message.

## Invariants
INV-003-01 — Without `--compress`, `chat` MUST NOT call the compression model or mark any turn compressed. Verified by: tests/test_spec003_chat.py::test_inv_003_01_no_compression_by_default
INV-003-02 — Without `--compress`, when the uncompressed turns exceed the phase token budget, `chat` MUST send all of them and print on stderr, even under `--quiet`: `warning: context ~<N> tokens exceeds the <phase> budget of <M>; nothing was compressed. Use --compress (may cause signal loss due to compression) or close and start a new session.` Verified by: test_inv_003_02_over_budget_warns_and_sends_all
INV-003-03 — With `--compress` and over budget, stderr MUST carry `warning: tail compression may cause signal loss due to compression` (even under `--quiet`). Only conversation turns (SPEC-001) older than the last two conversation turns may be compressed. Verified by: test_inv_003_03_compress_flag_warns
INV-003-04 — Compression MUST NOT mark compressed or rewrite any system turn, including `prior_decisions`, `prior_turns`, `agent_context`, `seed` and `resume`. Verified by: test_inv_003_04_compression_never_touches_system_turns
INV-003-05 — `prior_session` turns MUST be excluded unless `--prior-session-memory` is passed; `prior_decisions` and `prior_turns` MUST be included by default (backfill of Context Boundary v0 and PR #11). Verified by: tests/test_context_boundary_v0.py::test_no_prior_session_memory_excludes_injected_prior_memory, tests/test_prior_decisions_v0.py::test_chat_sends_prior_decisions_but_not_prior_session_prose, test_inv_003_05_prior_turns_included_by_default, test_prior_session_memory_disabled_retains_current_turns, test_prior_session_memory_enabled_retains_current_turns, test_prior_session_memory_does_not_duplicate_start_injection, test_prior_session_memory_is_optional, test_chat_records_turn_origins, test_prior_session_memory_filters_by_origin_without_markers, test_legacy_unknown_origin_rows_survive_migration, test_legacy_unknown_system_row_historical_looking_excluded, test_legacy_unknown_system_row_ordinary_looking_excluded, test_legacy_unknown_conversation_turn_remains_available, tests/test_integrated_boundary_v0.py::test_integrated_boundary_scenario_b_history_disabled
INV-003-06 — `CURRENT_GIT_STATE` MUST be observed at each invocation, sent after all stored turns, and printed on stdout, with `unavailable` for anything git cannot supply (DECISION-005, backfill). Verified by: tests/test_context_boundary_v0.py::test_chat_refreshes_git_state_at_invocation, test_chat_unavailable_git_state_does_not_substitute_head, test_chat_captures_fresh_git_state_on_each_invocation, test_chat_reports_unavailable_git_state, test_chat_authoritative_git_state_output_boundary, test_chat_current_git_state_includes_live_head_commit, test_chat_branch_present_status_failed_marks_dirty_unavailable, test_chat_branch_present_head_failed_does_not_fabricate_head, test_git_state_head_lookup_failure_is_explicit, test_chat_spoofed_model_current_state_does_not_replace_cli_observation, tests/test_integrated_boundary_v0.py::test_integrated_boundary_scenario_c_unavailable_observation, test_integrated_boundary_scenario_d_explicit_spoofed_model_output
INV-003-09 — The live git block sent and printed by `chat` MUST be the SPEC-002 block, including HEAD's subject and the changed paths. Verified by: test_inv_003_09_chat_sends_full_live_git_block
INV-003-10 — The history conflict line in `chat` MUST be computed from the historical turns actually sent in that request (SPEC-002 definition). Verified by: test_inv_003_10_chat_history_conflict_line
INV-003-07 — Stdin over 12 000 chars MUST be cut to 12 000, and the cut MUST be stated inside the text sent to the model: `[stdin truncated: first 12000 of <N> chars]`. Verified by: test_inv_003_07_stdin_truncation_visible_to_model
INV-003-08 — A refusal MUST exit with the code in the contract. Verified by: test_inv_003_08_no_active_session

## Out of scope
- The provider's context window. Ollama can drop the start of a long prompt without notice; detecting or preventing that needs its own decision (a follow-up violation under DECISION-006).
- The `impl`/`validate` tool loop.
- The `compress` command and `loose --synthesize`.
- How a model labels provenance in its reply.
