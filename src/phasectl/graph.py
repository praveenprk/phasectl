import re
import os
from pathlib import Path
from typing import Any

try:
    import kuzu
    KUZU_AVAILABLE = True
except ImportError:
    KUZU_AVAILABLE = False


class KuzuNotAvailableError(Exception):
    pass


class KuzuStore:
    def __init__(self, db_path: str | None = None):
        if not KUZU_AVAILABLE:
            raise KuzuNotAvailableError("kuzu not installed")

        if db_path is None:
            from .config import get_config_dir
            db_path = str(get_config_dir() / "graph.kuzu")

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db = kuzu.Database(db_path)
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Session(
                id STRING, project STRING, created_at STRING, PRIMARY KEY(id))
        """)
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS RFC(
                id STRING, project STRING, status STRING, title STRING, PRIMARY KEY(id))
        """)
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Decision(
                id STRING, project STRING, summary STRING, PRIMARY KEY(id))
        """)
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS Symbol(
                name STRING, file STRING, kind STRING, project STRING, PRIMARY KEY(name))
        """)
        self.conn.execute("CREATE REL TABLE IF NOT EXISTS PROGRESSED(FROM Session TO RFC)")
        self.conn.execute("CREATE REL TABLE IF NOT EXISTS LOGGED(FROM Session TO Decision)")
        self.conn.execute("CREATE REL TABLE IF NOT EXISTS TOUCHED(FROM Session TO Symbol)")

    def save_session_node(self, session_id: str, project: str, created_at: str) -> None:
        self.conn.execute(
            "MERGE (s:Session {id: $id, project: $project, created_at: $created_at})",
            {"id": session_id, "project": project, "created_at": created_at},
        )

    def save_rfc(self, rfc_id: str, project: str, status: str, title: str) -> None:
        self.conn.execute(
            "MERGE (r:RFC {id: $id, project: $project, status: $status, title: $title})",
            {"id": rfc_id, "project": project, "status": status, "title": title},
        )

    def save_decision(self, decision_id: str, project: str, summary: str) -> None:
        self.conn.execute(
            "MERGE (d:Decision {id: $id, project: $project, summary: $summary})",
            {"id": decision_id, "project": project, "summary": summary},
        )

    def link_session_rfc(self, session_id: str, rfc_id: str) -> None:
        self.conn.execute(
            "MATCH (s:Session {id: $sid}), (r:RFC {id: $rid}) MERGE (s)-[:PROGRESSED]->(r)",
            {"sid": session_id, "rid": rfc_id},
        )

    def link_session_decision(self, session_id: str, decision_id: str) -> None:
        self.conn.execute(
            "MATCH (s:Session {id: $sid}), (d:Decision {id: $did}) MERGE (s)-[:LOGGED]->(d)",
            {"sid": session_id, "did": decision_id},
        )

    def link_session_symbol(self, session_id: str, symbol_name: str) -> None:
        self.conn.execute(
            "MATCH (s:Session {id: $sid}), (sym:Symbol {name: $sym}) MERGE (s)-[:TOUCHED]->(sym)",
            {"sid": session_id, "sym": symbol_name},
        )

    def get_project_graph(self, project: str) -> dict[str, Any]:
        result = self.conn.execute(
            """
            MATCH (s:Session {project: $project})
            OPTIONAL MATCH (s)-[:PROGRESSED]->(r:RFC)
            OPTIONAL MATCH (s)-[:LOGGED]->(d:Decision)
            RETURN s.id, s.created_at, collect(DISTINCT r.id) as rfc_ids,
                   collect(DISTINCT r.status) as rfc_statuses,
                   collect(DISTINCT r.title) as rfc_titles,
                   collect(DISTINCT d.id) as decision_ids,
                   collect(DISTINCT d.summary) as decision_summaries
            ORDER BY s.created_at DESC
            """,
            {"project": project},
        )

        sessions = []
        while result.has_next():
            row = result.get_next()
            sessions.append({
                "id": row[0],
                "created_at": row[1],
                "rfcs": [
                    {"id": r[0], "status": r[1], "title": r[2]}
                    for r in zip(row[2], row[3], row[4]) if r[0]
                ],
                "decisions": [
                    {"id": d[0], "summary": d[1]}
                    for d in zip(row[5], row[6]) if d[0]
                ],
            })

        return {"sessions": sessions}

    def get_last_session_context(self, project: str) -> dict[str, Any]:
        result = self.conn.execute(
            """
            MATCH (s:Session {project: $project})-[:PROGRESSED]->(r:RFC)
            RETURN s.id, s.created_at, r.id, r.status, r.title
            ORDER BY s.created_at DESC LIMIT 5
            """,
            {"project": project},
        )

        sessions = []
        while result.has_next():
            row = result.get_next()
            sessions.append({
                "session_id": row[0],
                "created_at": row[1],
                "rfc_id": row[2],
                "rfc_status": row[3],
                "rfc_title": row[4],
            })

        return {"sessions": sessions}

    def extract_rfcs_and_decisions(self, summary: str) -> tuple[list[str], list[str]]:
        rfcs = re.findall(r"RFC-\d+", summary)
        decisions = re.findall(r"DECISION-\d+", summary)
        return list(dict.fromkeys(rfcs)), list(dict.fromkeys(decisions))

    def close(self):
        if hasattr(self, 'conn'):
            self.conn.close()
        if hasattr(self, 'db'):
            self.db.close()