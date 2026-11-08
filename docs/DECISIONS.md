# DECISIONS.md
# phasectl — Decisions Register
# Append only. A decision may be superseded. It is never removed.
# Format: ID — Title | Date | Refs | Decision | Reason | Risk | Status
#
# DECISION-002..005 are backfilled on 2026-11-08 from decisions already made
# in git history. Their reasons are only what the cited commits and docs
# record. Nothing is reconstructed beyond that.

---

## DECISION-001 — Adopt eng-principles

Date    : 2026-11-08
Refs    : eng-principles ENG-DEC-001, ENG-STD-001..007 (at eng-principles 122672d)
Decision: phasectl adopts eng-principles. Registers live in docs/:
          DECISIONS.md, VIOLATIONS.md, conformance/phasectl-ICS.md. Hooks are
          vendored from eng-principles hooks/ into scripts/hooks/, with
          phasectl's own `anchors` policy. Agent instructions live in AGENTS.md.
          Gaps found on adoption are registered as V-ENG-NNN-NN (violations of
          an ENG standard, numbered by standard).
Reason  : One discipline across all projects (eng-principles ENG-DEC-001).
          Adoption makes phasectl's actual state visible instead of implied.
Risk    : Commits before adoption stay unanchored. History is not rewritten.
Status  : ACCEPTED

---

## DECISION-002 — The user's codebase is read-only

Date    : 2026-08-02 (backfilled 2026-11-08)
Refs    : commit ea27831; README.md §Security ("Never writes to your codebase"); docs/phasectl.1
Decision: phasectl reads git state and indexed symbols. It never writes,
          modifies, or deletes files in the user's codebase.
Reason  : As recorded in ea27831: "codify read-only codebase invariant".
Risk    : none recorded
Status  : ACCEPTED

---

## DECISION-003 — The indexer is a plain binary on PATH, not an MCP server

Date    : 2026-08-13 (backfilled 2026-11-08)
Refs    : commit ccb6244 (PR #2)
Decision: phasectl shells out to `<indexer> index <path>` and
          `<indexer> query <term>`. The MCP JSON-RPC handshake and dynamic tool
          discovery are removed. The config key `tools.mcp_command` becomes
          `tools.indexer_command`. The tool loop exposes one fixed tool,
          `search_symbols`.
Reason  : As recorded in ccb6244: it mirrors the pattern `phasectl index`
          already used, and "no AI is involved in indexing/query dispatch".
          (It is consistent with eng-principles ENG-P-13: plain binary, plain contract.)
Risk    : none recorded
Status  : ACCEPTED

---

## DECISION-004 — Providers are swappable behind one interface

Date    : backfilled 2026-11-08 (original date not recorded)
Refs    : src/phasectl/provider.py `LLMProvider`; CONTRIBUTING.md §Add a provider
Decision: Every LLM backend implements the `LLMProvider` ABC (chat, compress)
          and is selected via `get_provider()`. User-facing strings stay
          provider-agnostic.
Reason  : As recorded in README/CONTRIBUTING: provider-agnostic operation
          (Anthropic, OpenAI, Ollama, OpenRouter, OpenAI-compatible).
Risk    : none recorded
Status  : ACCEPTED

---

## DECISION-005 — Resume context is grounded in deterministic git state

Date    : 2026-10-26 (backfilled 2026-11-08)
Refs    : commit 11efffb (PR #7); tests/test_context_boundary_v0.py, tests/test_integrated_boundary_v0.py, tests/test_resume_grounding.py
Decision: Git state in the resume block is computed outside LLM synthesis.
          Fabricated git-state defaults are eliminated, and provenance is
          preserved when assembling provider messages.
Reason  : As recorded in 11efffb ("Ground resume block Git state outside LLM
          synthesis", "Eliminate fabricated Git-state defaults").
Risk    : none recorded
Status  : ACCEPTED
