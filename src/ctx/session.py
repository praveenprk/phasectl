from dataclasses import dataclass
from typing import Optional
import uuid
from datetime import datetime

from .store import SQLiteStore


@dataclass
class Session:
    id: str
    project: str
    created_at: str
    closed_at: Optional[str]
    opening_phase: str
    closing_phase: Optional[str]
    final_summary: Optional[str]
    total_tokens: int


def open_session(store: SQLiteStore, project: str, phase: str) -> Session:
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    store.create_session(id=session_id, project=project, phase=phase, created_at=now)
    return Session(
        id=session_id,
        project=project,
        created_at=now,
        closed_at=None,
        opening_phase=phase,
        closing_phase=None,
        final_summary=None,
        total_tokens=0,
    )


def close_session(store: SQLiteStore, session_id: str, phase: str, summary: str, total_tokens: int) -> None:
    store.close_session(id=session_id, phase=phase, summary=summary, total_tokens=total_tokens)


def load_prior_session(store: SQLiteStore, project: str) -> Optional[tuple[dict, list[dict]]]:
    session_data = store.get_last_session(project)
    if not session_data:
        return None
    turns = store.get_last_uncompressed_turns(session_data["id"], 3)
    return (session_data, turns)
