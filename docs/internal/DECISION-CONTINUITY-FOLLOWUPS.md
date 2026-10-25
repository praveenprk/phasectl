# Decision Continuity follow-ups

## Cross-store SQLite/graph consistency

**Status:** recorded separately; no implementation in Decision Continuity v0.

`close()` currently commits the closed session and final summary to SQLite before
calling `persist_session_graph()`. The two stores do not share a transaction.

Failure modes:

- A graph initialization failure is silently ignored, so SQLite can contain a
  closed snapshot while no graph update is attempted.
- A graph write failure after the SQLite commit can leave the graph missing the
  session or containing only partially written nodes and edges. The session is
  already closed, so invoking `close()` again is not a recovery path.
- Reversing the write order without additional design would only invert the
  failure window: a SQLite failure could leave graph records for a session that
  remains active.
- Process termination between stores can produce either ordering's partial
  state even when individual writes succeed.

A follow-up must define explicit failure reporting and a retry/reconciliation or
compensation strategy before changing persistence behavior. Original SQLite
turns remain the source record for rebuilding derived graph state.
