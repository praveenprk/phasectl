# Native C++ indexer — implementation plan (issue #3)

Tracks github.com/praveenprk/phasectl issue #3: remove the npm dependency
(`@colbymchenry/codegraph`) that phasectl's default `[tools].indexer_command`
currently shells out to (see PR #2), replacing it with a single static
binary that needs no runtime — no Node, no interpreter, no package manager
at query time. Fits the project's stated "Unix citizen" design philosophy
(README, Design philosophy section) better than an npm-distributed tool
ever will.

This is deliberately slow, one-milestone-at-a-time work. Not the Rust
rewrite (issue #4) — that's phasectl itself, later, after an audit. This
plan is scoped to the indexer binary only, and stays useful even if/when
phasectl itself becomes Rust, since the contract below is language-agnostic
(plain subprocess, stdin/stdout, no protocol).

**Implementation language: C++. No exceptions, no runtime of any other
kind involved.** CMake + C++ only (M1) — no Python, no Node, no scripting
glue anywhere in the build or the binary. Where "language" comes up again
later in this plan (M4, M7, and the first open question below), it means
the *source language the indexer parses* — the code it reads out of a
target project like phasectl or contextos — which is a completely separate
axis from what the indexer itself is written in. Don't conflate the two;
that ambiguity is exactly what this note exists to kill.

## Contract to match

Verified live against CodeGraph in PR #2 (`src/phasectl/tools.py`,
`src/phasectl/cli.py::_run_index`/`_summarize_index`). The new binary must
satisfy the same shape, but **should simplify where CodeGraph didn't**:

- `<binary> index <path>` — build (or rebuild) the index for the project
  rooted at `<path>`. **Should be idempotent and self-initializing** — no
  separate `init` step. CodeGraph requires `init` before `index` will work
  on a fresh project (a real footgun we had to special-case around in
  `_run_index`'s fallback logic); the native binary shouldn't reproduce
  that split. Exit 0 on success.
- `<binary> query <search> [--path <dir>] [--json] [--limit N] [--kind K]`
  — search the index for `<dir>` (or cwd if `--path` omitted — but phasectl
  always passes `--path`, since it invokes indexers from wherever it's
  running, not from inside the project). Exit 0 with `[]` (JSON) or empty
  stdout on no matches; exit 0 as `phasectl query`'s `--json`/plain
  behavior maps 1:1 onto whatever this prints, so plain output should stay
  human-scannable (`kind  name` / file:line / signature, one match per
  block — see current CodeGraph plain output for the bar to match).
- `<binary> status <path> --json` — optional but valuable: if present,
  `phasectl index`'s `_summarize_index` reads `fileCount`/`nodeCount` from
  it automatically, no phasectl-side change needed. Skip it and phasectl
  falls back to parsing `index`'s own stdout, which is a worse summary.
- Errors go to **stderr**, non-zero exit code. Stdout stays clean data only
  (this is what lets `phasectl query --json` pipe raw stdout straight
  through).

## Milestones

One commit per milestone, on this branch (`native-cpp-indexer`). Check the
box and commit the plan-file edit together with the code in the same
commit, so this file is always an honest snapshot of what's actually done.

- [ ] **M1 — CLI skeleton.** CMake project, builds a binary that parses
      `index <path>` and `query <search> [--path] [--json] [--limit] [--kind]`
      per the contract above. No real indexing yet: `index` always
      succeeds and does nothing; `query` always prints `[]`/nothing.
      Done when: `cmake --build . && ./indexer index . && ./indexer query
      foo --json` prints `[]` and exits 0.
- [ ] **M2 — File discovery.** Walk the project tree, skip `.git` and a
      small hardcoded vendor/ignore list (`node_modules`, `.venv`,
      `__pycache__`, `dist`, `build`, `.codegraph`), filter to a source
      extension allowlist. No storage yet — `index` just counts files.
      Done when: file count is correct on phasectl's own repo (should
      match CodeGraph's `fileCount` of 16 for reference, modulo language
      allowlist differences).
- [ ] **M3 — On-disk index store.** SQLite (C API — no extra dependency
      beyond libsqlite3, which is preinstalled on macOS and trivially
      available on Linux) schema for symbols: `id, kind, name,
      qualified_name, file_path, start_line, end_line, signature`. Storage
      location is settled (see "Resolved decisions" below): XDG cache
      dir, not project-local — nothing gets written under the indexed
      project's own tree.
- [ ] **M4 — Naive symbol extraction, one language.** Regex-based
      extraction of function/class/method definitions. Start by parsing
      the indexer's own C++ source (self-hosted dogfooding — index this
      project with itself) before branching out to other source
      languages. No AST, no cross-file resolution — that's explicitly out
      of scope for a first cut.
- [ ] **M5 — Query implementation.** Case-insensitive substring match
      against `name`, ranked exact-match-first. `--json` output shaped
      close enough to CodeGraph's `node.{kind,name,qualifiedName,
      filePath,startLine,endLine,signature}` that nothing in
      `src/phasectl/tools.py` needs to change to consume it — or, if it
      diverges, that divergence gets documented there explicitly.
- [ ] **M6 — `status --json`.** `fileCount`/`nodeCount` at minimum, so
      `phasectl index`'s summary line picks it up for free.
- [ ] **M7 — More source languages.** Expand M4's regex extraction to the
      languages phasectl's own test targets actually use — Python and
      JavaScript first (matches what we exercised live against phasectl
      and contextos in the PR #2 walkthrough), then whatever else comes
      up. Tree-sitter would be the "real" answer but is itself a
      dependency to pull in — leave that decision for when this milestone
      is actually reached, not now.
- [ ] **M8 — Packaging.** Static binary build (macOS + Linux), Homebrew
      formula. This is what makes `brew install <name>` real.
- [ ] **M9 — Swap the default.** Once this binary has real feature parity
      with CodeGraph for the query patterns phasectl actually exercises
      (see the live walkthrough in PR #2's description), flip
      `_DEFAULT_INDEXER_COMMAND` in `src/phasectl/tools.py` and the
      README's default. Keep CodeGraph documented as a swappable option,
      same as any other indexer under `[tools].indexer_command`.

## Resolved decisions

- **Index storage location: XDG cache dir, not project-local.**
  `$XDG_CACHE_HOME/<binary-name>/<project-key>/index.db` (default
  `~/.cache/<binary-name>/...` on any POSIX system — no macOS
  `~/Library/Caches` special-casing; phasectl itself already treats macOS
  as just another Unix box for its own `~/.config/phasectl`, and this
  follows the same instinct). `<project-key>` derived from the absolute
  project path (path-mirrored or hashed — pick one at M3). This is what
  most Unix-native tools that build a rebuildable local index actually do
  (ccache, cargo's registry cache, pip's wheel cache) — cache directories
  are for exactly this: derived data you can nuke and rebuild, kept out
  of the way of the project it describes. It also makes the "phasectl
  never writes to your codebase" invariant (README, Design philosophy)
  literal rather than aspirational, unlike CodeGraph's `.codegraph/`
  convention which does write into the project tree. Settled — M3
  implements this, doesn't re-litigate it.

## Open questions

Resolve each at the milestone that first needs an answer — don't
pre-decide them here:

- **First non-self source language for M7**: M4 dogfoods on the
  indexer's own C++ source; plan above suggests Python/JavaScript next
  since that's what we exercised live in the PR #2 walkthrough (phasectl,
  contextos), but worth a second look if the primary target audience
  skews differently. (Reminder: this is about what source code the
  indexer *parses*, not what the indexer is *written in* — the binary is
  C++ throughout, that part isn't in question.)
- **Binary/project name**: not yet chosen. Needs one before M8 packaging.

## Workflow for picking this up

Read this file top to bottom, then `git log --oneline` on this branch to
see which milestones are already committed (checked boxes = done). Pick up
the next unchecked milestone, implement only that milestone, and stop —
don't cascade into the next one in the same session unless explicitly
asked to. If a milestone surfaces a design question not already listed
above, add it there and stop for a decision instead of guessing.

### Starter prompt (paste into a new session)

```
We're building a native C++ code indexer for phasectl, tracked as issue #3
(github.com/praveenprk/phasectl) and planned in
docs/internal/NATIVE_INDEXER_PLAN.md on the `native-cpp-indexer` branch.

Read that plan file first, then `git log --oneline` on this branch to see
which milestones are already committed (checked boxes in the plan file =
done, one commit each). Pick up the next unchecked milestone only,
implement it, and commit the code together with checking its box in the
plan file in the same commit. Don't start the following milestone in the
same session unless I explicitly ask.

If the milestone raises a design question not already answered under
"Open questions" in the plan file, stop and ask me rather than guessing —
this is meant to be slow, deliberate work, not a sprint.
```
