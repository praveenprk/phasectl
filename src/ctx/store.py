from typing import Protocol, Optional
import sqlite3
import os
from datetime import datetime


class AbstractStore(Protocol):
    def create_session(self, id: str, project: str, phase: str, created_at: str) -> None: ...

    def get_session(self, id: str) -> Optional[dict]: ...

    def update_session(self, id: str, **kwargs) -> None: ...

    def close_session(self, id: str, phase: str, summary: str, total_tokens: int) -> None: ...

    def add_turn(self, session_id: str, phase: str, role: str, content: str, token_estimate: int) -> int: ...

    def get_turns(self, session_id: str) -> list[dict]: ...

    def get_active_session(self, project: str) -> Optional[dict]: ...

    def get_last_session(self, project: str) -> Optional[dict]: ...

    def get_uncompressed_turns(self, session_id: str) -> list[dict]: ...

    def mark_turns_compressed(self, turn_ids: list[int]) -> None: ...

    def get_last_uncompressed_turns(self, session_id: str, n: int) -> list[dict]: ...


class SQLiteStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              TEXT PRIMARY KEY,
                project         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                closed_at       TEXT,
                opening_phase   TEXT NOT NULL,
                closing_phase   TEXT,
                final_summary   TEXT,
                total_tokens    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS turns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL REFERENCES sessions(id),
                phase           TEXT NOT NULL,
                role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content         TEXT NOT NULL,
                token_estimate  INTEGER NOT NULL,
                timestamp       TEXT NOT NULL,
                compressed      INTEGER NOT NULL DEFAULT 0
            );
        """)

    def create_session(self, id: str, project: str, phase: str, created_at: str) -> None:
        self.conn.execute(
            "INSERT INTO sessions (id, project, created_at, opening_phase) VALUES (?, ?, ?, ?)",
            (id, project, created_at, phase),
        )
        self.conn.commit()

    def get_session(self, id: str) -> Optional[dict]:
        cursor = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_session(self, id: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [id]
        self.conn.execute(f"UPDATE sessions SET {sets} WHERE id = ?", values)
        self.conn.commit()

    def close_session(self, id: str, phase: str, summary: str, total_tokens: int) -> None:
        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE sessions SET closed_at = ?, closing_phase = ?, final_summary = ?, total_tokens = ? WHERE id = ?",
            (now, phase, summary, total_tokens, id),
        )
        self.conn.commit()

    def add_turn(self, session_id: str, phase: str, role: str, content: str, token_estimate: int) -> int:
        now = datetime.now().isoformat()
        cursor = self.conn.execute(
            "INSERT INTO turns (session_id, phase, role, content, token_estimate, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, phase, role, content, token_estimate, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_turns(self, session_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_active_session(self, project: str) -> Optional[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM sessions WHERE project = ? AND closed_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (project,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_last_session(self, project: str) -> Optional[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM sessions WHERE project = ? AND closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1",
            (project,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_uncompressed_turns(self, session_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE session_id = ? AND compressed = 0 ORDER BY timestamp ASC",
            (session_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_turns_compressed(self, turn_ids: list[int]) -> None:
        if not turn_ids:
            return
        placeholders = ",".join("?" for _ in turn_ids)
        self.conn.execute(
            f"UPDATE turns SET compressed = 1 WHERE id IN ({placeholders})",
            turn_ids,
        )
        self.conn.commit()

    def get_last_uncompressed_turns(self, session_id: str, n: int) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE session_id = ? AND compressed = 0 ORDER BY timestamp DESC LIMIT ?",
            (session_id, n),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in reversed(rows)]
