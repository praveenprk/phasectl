import re
from pathlib import Path
from typing import Any

import kuzu

from .config import get_config_dir


class KuzuStore:
    def __init__(self, db_path: str | None = None):
        if db_path is None or db_path == "":
            db_path = str(get_config_dir() / "graph.kuzu")
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
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS PROGRESSED(FROM Session TO RFC)
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS LOGGED(FROM Session TO Decision)
        """)
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS TOUCHED(FROM Session TO Symbol)
        """)

    def save_session_node(self, session_id: str, project: str, created_at: str) -> None:
        self.conn.execute(
            "MERGE (s:Session {id: $id}) ON CREATE SET s.project = $project, s.created_at = $created_at",
            {"id": session_id, "project": project, "created_at": created_at},
        )

    def save_rfc(self, rfc_id: str, project: str, status: str, title: str) -> None:
        self.conn.execute(
            "MERGE (r:RFC {id: $id}) ON CREATE SET r.project = $project, r.status = $status, r.title = $title",
            {"id": rfc_id, "project": project, "status": status, "title": title},
        )

    def save_decision(self, decision_id: str, project: str, summary: str) -> None:
        self.conn.execute(
            "MERGE (d:Decision {id: $id}) ON CREATE SET d.project = $project, d.summary = $summary",
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

    def get_project_graph(self, project: str) -> dict:
        sessions = self.conn.execute(
            "MATCH (s:Session {project: $p}) RETURN s.id, s.created_at ORDER BY s.created_at DESC",
            {"p": project},
        ).get_as_df()

        rfc_data = self.conn.execute(
            "MATCH (s:Session {project: $p})-[:PROGRESSED]->(r:RFC) RETURN s.id, r.id, r.status, r.title",
            {"p": project},
        ).get_as_df()

        decision_data = self.conn.execute(
            "MATCH (s:Session {project: $p})-[:LOGGED]->(d:Decision) RETURN s.id, d.id, d.summary",
            {"p": project},
        ).get_as_df()

        sessions_list = []
        for _, row in sessions.iterrows():
            sid = row["s.id"]
            rfc_rows = rfc_data[rfc_data["s.id"] == sid]
            dec_rows = decision_data[decision_data["s.id"] == sid]
            sessions_list.append({
                "id": sid,
                "created_at": row["s.created_at"],
                "rfcs": [
                    {"id": r["r.id"], "status": r["r.status"], "title": r["r.title"]}
                    for _, r in rfc_rows.iterrows()
                ],
                "decisions": [
                    {"id": d["d.id"], "summary": d["d.summary"]}
                    for _, d in dec_rows.iterrows()
                ],
            })

        return {"sessions": sessions_list}

    def get_last_session_context(self, project: str) -> dict:
        result = self.conn.execute(
            """
            MATCH (s:Session {project: $p})-[:PROGRESSED]->(r:RFC)
            RETURN s.id, s.created_at, r.id, r.status, r.title
            ORDER BY s.created_at DESC LIMIT 5
            """,
            {"p": project},
        ).get_as_df()

        context = []
        for _, row in result.iterrows():
            context.append({
                "session_id": row["s.id"],
                "created_at": row["s.created_at"],
                "rfc_id": row["r.id"],
                "rfc_status": row["r.status"],
                "rfc_title": row["r.title"],
            })
        return {"orient_context": context}

    def extract_refs_from_summary(self, summary: str) -> tuple[list[str], list[str]]:
        rfcs = re.findall(r"RFC-(\d+)", summary)
        decisions = re.findall(r"DECISION-(\d+)", summary)
        return [f"RFC-{r}" for r in rfcs], [f"DECISION-{d}" for d in decisions]

    def close(self) -> None:
        self.conn.close()
        self.db.close()