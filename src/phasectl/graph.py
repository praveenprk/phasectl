import json
import os
import re
from pathlib import Path
from typing import Any

import networkx as nx


class GraphStore:
    """JSON-backed NetworkX MultiDiGraph.

    Node keys are namespaced: 'session:<id>', 'rfc:<id>', 'decision:<id>',
    'symbol:<name>'. Nodes carry a 'kind' attribute plus type-specific fields.
    Edges carry a 'rel' attribute (PROGRESSED, LOGGED, TOUCHED).
    File at rest is nx.node_link_data JSON — inspectable, structured.
    """

    def __init__(self, path: str | None = None):
        if path is None:
            from .config import get_config_dir
            path = str(get_config_dir() / "graph.json")
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.G = self._load()

    def _load(self) -> nx.MultiDiGraph:
        if not os.path.exists(self.path):
            return nx.MultiDiGraph()
        try:
            with open(self.path) as f:
                data = json.load(f)
            return nx.node_link_graph(
                data, directed=True, multigraph=True, edges="edges", name="_node_key"
            )
        except (json.JSONDecodeError, OSError, KeyError):
            return nx.MultiDiGraph()

    def _save(self) -> None:
        data = nx.node_link_data(self.G, edges="edges", name="_node_key")
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def save_session_node(self, session_id: str, project: str, created_at: str) -> None:
        self.G.add_node(
            f"session:{session_id}",
            kind="Session",
            id=session_id,
            project=project,
            created_at=created_at,
        )
        self._save()

    def save_rfc(self, rfc_id: str, project: str, status: str, title: str) -> None:
        self.G.add_node(
            f"rfc:{rfc_id}",
            kind="RFC",
            id=rfc_id,
            project=project,
            status=status,
            title=title,
        )
        self._save()

    def save_decision(self, decision_id: str, project: str, summary: str) -> None:
        self.G.add_node(
            f"decision:{decision_id}",
            kind="Decision",
            id=decision_id,
            project=project,
            summary=summary,
        )
        self._save()

    def save_symbol(self, name: str, file: str, kind: str, project: str) -> None:
        self.G.add_node(
            f"symbol:{name}",
            kind="Symbol",
            name=name,
            file=file,
            symbol_kind=kind,
            project=project,
        )
        self._save()

    def _add_edge(self, src: str, dst: str, rel: str) -> None:
        if src not in self.G.nodes or dst not in self.G.nodes:
            return
        for _, _, data in self.G.out_edges(src, data=True):
            if data.get("rel") == rel:
                continue
        for _, existing_dst, data in self.G.out_edges(src, data=True):
            if existing_dst == dst and data.get("rel") == rel:
                return
        self.G.add_edge(src, dst, rel=rel)
        self._save()

    def link_session_rfc(self, session_id: str, rfc_id: str) -> None:
        self._add_edge(f"session:{session_id}", f"rfc:{rfc_id}", "PROGRESSED")

    def link_session_decision(self, session_id: str, decision_id: str) -> None:
        self._add_edge(f"session:{session_id}", f"decision:{decision_id}", "LOGGED")

    def link_session_symbol(self, session_id: str, symbol_name: str) -> None:
        self._add_edge(f"session:{session_id}", f"symbol:{symbol_name}", "TOUCHED")

    def get_project_graph(self, project: str) -> dict[str, Any]:
        sessions = []
        for node, attrs in self.G.nodes(data=True):
            if attrs.get("kind") != "Session" or attrs.get("project") != project:
                continue
            rfcs: list[dict] = []
            decisions: list[dict] = []
            for _, target, edge_attrs in self.G.out_edges(node, data=True):
                rel = edge_attrs.get("rel")
                t = self.G.nodes[target]
                if rel == "PROGRESSED" and t.get("kind") == "RFC":
                    rfcs.append({
                        "id": t.get("id", ""),
                        "status": t.get("status", ""),
                        "title": t.get("title", ""),
                    })
                elif rel == "LOGGED" and t.get("kind") == "Decision":
                    decisions.append({
                        "id": t.get("id", ""),
                        "summary": t.get("summary", ""),
                    })
            sessions.append({
                "id": attrs.get("id", ""),
                "created_at": attrs.get("created_at", ""),
                "rfcs": rfcs,
                "decisions": decisions,
            })
        sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return {"sessions": sessions}

    def get_last_session_context(self, project: str) -> dict[str, Any]:
        full = self.get_project_graph(project)
        sessions = []
        for s in full["sessions"][:5]:
            for r in s.get("rfcs", []):
                sessions.append({
                    "session_id": s["id"],
                    "created_at": s["created_at"],
                    "rfc_id": r["id"],
                    "rfc_status": r["status"],
                    "rfc_title": r["title"],
                })
        return {"sessions": sessions}

    def extract_rfcs_and_decisions(self, summary: str) -> tuple[list[str], list[str]]:
        rfcs = re.findall(r"RFC-\d+", summary)
        decisions = re.findall(r"DECISION-\d+", summary)
        return list(dict.fromkeys(rfcs)), list(dict.fromkeys(decisions))

    def close(self) -> None:
        pass


KuzuStore = GraphStore
