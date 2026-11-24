# QUESTIONS.md — phasectl

Open questions for the governor, one entry per question, in the format of
eng-principles templates/question.md (ENG-DEC-010, refined by ENG-DEC-012;
ENG-STD-005 §2.4). An entry is added by the pull request that raises it,
and the governor answers on that pull request. The `commit-check /
questions` job (eng-principles hooks/check-questions, run through
.github/workflows/commit-check.yml) fails while any entry is OPEN. An
answer that changes phasectl's behaviour or policy becomes a DECISION entry
in docs/DECISIONS.md. Entries are never deleted (ENG-STD-006 §2.1).

Format:

    ### Q-<NNN>
    Scenario : <what a real user does, in what situation; no code>
    Options  : A — <choice>: <what it changes>
               B — <choice>: <what it changes>
    Recommend: <A or B>, because <reason>
    Raised by: <the pull request into main that raises it>
    Status   : OPEN | ANSWERED (governor, YYYY-MM-DD): <answer> → <DECISION-NNN, or "no change">

---

No questions yet.
