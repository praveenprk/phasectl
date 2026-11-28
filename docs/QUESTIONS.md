# QUESTIONS.md — phasectl

Open questions for the governor, one entry per question, in the format of
eng-principles templates/question.md (ENG-DEC-010, refined by ENG-DEC-012;
ENG-STD-005 §2.4). An entry is added by the pull request that raises it,
and the governor answers on that pull request. The `commit-check /
questions` job (eng-principles hooks/check-questions, run through
.github/workflows/commit-check.yml) fails while any entry is OPEN. An
answer that changes phasectl's behaviour or policy becomes a DECISION entry
in docs/DECISIONS.md. Entries are never deleted (ENG-STD-006 §2.1).
A question a contributor raises is answered by its Recommend option under the
governor's standing rule (DECISION-007); a pull request that records such an
answer is merged by the governor, as a security-related change is.

Format:

    ### Q-<NNN>
    Scenario : <what a real user does, in what situation; no code>
    Options  : A — <choice>: <what it changes>
               B — <choice>: <what it changes>
    Recommend: <A or B>, because <reason>
    Raised by: <the pull request into main that raises it>
    Status   : OPEN | ANSWERED (governor, YYYY-MM-DD): <answer> → <DECISION-NNN, or "no change">

---

### Q-001
Scenario : The governor releases phasectl after the move to `main` and
           `stable`. `main` keeps linear history: pull requests merge by
           rebase and the ruleset requires it. The governor now opens a
           `main` → `stable` pull request to promote the release, and has to
           choose how it lands on `stable`.
Options  : A — Merge commit: `stable` gets one merge commit per promotion,
               whose second parent is the `main` commit released. `main`
               stays linear because nothing merges back into it. The `stable`
               ruleset allows Merge only and does not require linear history.
               Same as eng-principles and factory-protocol (ENG-DEC-012).
           B — Linear `stable`: `stable` is fast-forwarded to the released
               `main` commit, so both branches stay linear and share commits.
               GitHub pull requests cannot fast-forward, so the governor
               pushes `stable` directly. Its ruleset needs the governor as
               a bypass actor, and the promotion has no pull request.
Recommend: A, because the promotion stays a reviewed pull request with
           required checks, each merge commit marks exactly one release on
           `stable`, it matches the other repositories, and the linear-history
           rule still holds where it matters: on `main`, where work lands.
Raised by: this pull request (docs/ENG-DEC-012-main-stable-roles)
Status   : ANSWERED (governor, standing rule 2026-11-25): A → no change (applies ENG-DEC-012; DECISION-007)

### Q-002
Scenario : A contributor runs the field test (`scripts/field-test.sh`, or
           the runner in tests/field) to check phasectl against a real
           repository. Until now the runner fell back to a fixed path, a
           private project on the maintainer's machine, when no repository
           was named.
Options  : A — No default: the contributor names the repository with
               `--repo PATH` or `PHASECTL_FIELD_REPO`. Without one, the
               script prints how to name it and exits 2, and the runner
               raises an error. The private project's name leaves the repo.
           B — Keep the default path: it works with no arguments on the
               maintainer's machine and fails with "external repository does
               not exist" everywhere else.
Recommend: A, because the default only works on one machine, names a private
           project in a public repository, and makes every other contributor
           discover the flag by failing.
Raised by: this pull request (chore/DECISION-001-cleanup)
Status   : ANSWERED (governor, standing rule 2026-11-25): A → no change (DECISION-007)

### Q-003
Scenario : A user runs `phasectl close`. The session is stored, but the graph
           store cannot be opened or a graph write fails.
Options  : A — Print `warning: session graph not updated: <exception class>: <message>`
           on stderr, exit 0, and keep the one stdout line. The session is
           closed and SQLite holds it.
           B — Print the same message as `error: …` and exit 1. The session is
           already closed, so re-running `close` cannot retry.
Recommend: A, because the session's record is already safe in SQLite, a
           non-zero exit would report a close that succeeded as failed, and
           re-running cannot recover.
Raised by: this pull request (ns/phasectl-v00401-graph-warning)
Status   : ANSWERED (governor, standing rule 2026-11-25): A → no change (DECISION-007)
