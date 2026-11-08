# RFC-001 — Lossless context by default
Status: OPEN (no open questions; closes on governor approval) · Date: 2026-11-08

## Problem

phasectl's value is continuity and authority across sessions (DECISION-005,
docs/internal/DESIGN.md). Today, four steps on the default path ask a model
to rewrite context before the next session or the next request sees it:

| Step | Where | What a model rewrites |
|------|-------|-----------------------|
| Snapshot | `close` → `api_compress` with the snapshot phase prompt | the whole session, into `final_summary` |
| Resume synthesis | `start --phase orient` → `resume.build_resume_block` | git state + last snapshot + coding-agent transcript |
| Seed gist | `start --seed` → `seed.summarize_seed` | each seed file |
| Tail compression | `chat` → `context.compress_tail`, automatic over the phase token budget | all but the last two turns, any origin |

A rewrite can drop or alter facts and nothing reports it. Experiment 002
(internal, git-excluded `phasectl-exp-001/`) measured one case: the resume
synthesis dropped decision IDs in 5/6 trials, and session 2 recalled 0/12
decisions. PR #11 patched decisions only (V-ENG-002-02). The snapshot, seed
and compression steps carry the same risk, and `compress_tail` can rewrite
the PRIOR_DECISIONS turn itself.

The governor's requirement (2026-11-08): phasectl must not lose signal and
must be correct throughout. Compression should sit behind a flag that warns
it may lose signal.

## New contract, or more detail on an existing one?

New contract. No SPEC exists for `close`, `start` or `chat` (V-ENG-002-01).
This RFC sets the rule; SPEC-001 (`close`), SPEC-002 (`start`) and SPEC-003
(`chat` context assembly) state it as invariants. They also backfill the
shipped behaviour they keep, including PR #11's PRIOR_DECISIONS
(V-ENG-002-02).

## Options considered

1. **Keep model steps on by default; improve prompts and recover what they
   drop** (the PR #9 / PR #11 pattern). Rejected: each fix covers one kind
   of fact. Everything not recovered can still be lost silently.
2. **Remove model packaging entirely.** Rejected: summaries are useful on
   demand, and some sessions outgrow a model's context.
3. **Deterministic by default, model packaging opt-in and labelled.** On the
   default path the CLI copies verbatim or computes. Model rewrites run only
   behind a flag. Each one warns on stderr, is labelled model-derived, and is
   added next to the verbatim source, never in place of it. Chosen.

## Proposed answer

Option 3, recorded as DECISION-006:

- `close` stores a deterministic snapshot: decision lines verbatim from the
  original turns, the references found, and the last exchange verbatim.
  `close --summarize` adds a labelled model summary after it.
- `start` builds PRIOR_DECISIONS from the prior session's original turns, not
  from a model summary. It adds the prior session's last exchange verbatim.
  In orient it adds the coding-agent transcript as a labelled verbatim
  excerpt. `--seed` files go in verbatim. `start --summarize` adds the model
  resume synthesis and seed gists as extra, labelled turns.
- `chat` never compresses unless `--compress` is passed. Over budget, it
  sends everything and warns. Compression never rewrites CLI-owned turns.
- Every opt-in model step prints to stderr:
  `warning: <step> may cause signal loss due to compression`.
- Where a size cap applies (seed 200 KB, coding-agent excerpt, stdin 12 000
  chars), the cut is stated in the text the model sees, never silent.

Known gaps not fixed here are registered or listed as out of scope in the
SPECs: the provider-side context window (Ollama truncates long prompts
without notice), the `compress` command and `loose --synthesize`, and
small models misreporting provenance.

## Open questions

None.
