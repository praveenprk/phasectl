"""Deterministic session handoff (SPEC-001, SPEC-002; DECISION-006).

Everything here is computed from stored turns by the CLI. No model call.
Text is copied verbatim: lines are selected, never rewritten or truncated.
"""

import re

from .graph import _FENCE_RE, _strip_inline_code, extract_decision_references

CARRIED_ORIGIN = "prior_decisions"
_RFC_OR_DECISION_RE = re.compile(
    r"(?<![\w-])(?:RFC-\d+|DECISION-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?![\w-])"
)


def is_conversation_turn(turn: dict) -> bool:
    origin = turn.get("origin")
    if origin in ("user", "assistant"):
        return True
    return origin is None and turn.get("role") in ("user", "assistant")


def source_turns(turns: list[dict]) -> list[dict]:
    return [
        t for t in turns
        if t.get("phase") != "_compression"
        and (is_conversation_turn(t) or t.get("origin") == CARRIED_ORIGIN)
    ]


def _visible_lines(text: str) -> list[tuple[str, str]]:
    """(original line, visible prose of that line) for lines outside code fences."""
    pairs = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if fence_char:
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_length
                and not match.group(2).strip()
            ):
                fence_char = ""
                fence_length = 0
            continue
        if match:
            fence_char = match.group(1)[0]
            fence_length = len(match.group(1))
            continue
        pairs.append((line, _strip_inline_code(line)))
    return pairs


def _strip_one_bullet(line: str) -> str:
    if len(line) >= 2 and line[0] in "-*•" and line[1] == " ":
        return line[2:].lstrip()
    return line


def decision_lines(turns: list[dict]) -> list[str]:
    lines: list[str] = []
    for turn in source_turns(turns):
        carried = turn.get("origin") == CARRIED_ORIGIN
        for original, visible in _visible_lines(turn.get("content") or ""):
            if not extract_decision_references(visible):
                continue
            line = _strip_one_bullet(original.strip())
            if not carried:
                line = f"[{turn.get('role')}] {line}"
            if line not in lines:
                lines.append(line)
    return lines


def references(turns: list[dict]) -> list[str]:
    refs: list[str] = []
    for turn in source_turns(turns):
        for _, visible in _visible_lines(turn.get("content") or ""):
            for ref in _RFC_OR_DECISION_RE.findall(visible):
                if ref not in refs:
                    refs.append(ref)
    return refs


def last_conversation_turns(turns: list[dict], n: int = 2) -> list[dict]:
    conversation = [
        t for t in turns
        if t.get("phase") != "_compression" and is_conversation_turn(t)
    ]
    return conversation[-n:] if n else []


def _format_turns(turns: list[dict]) -> str:
    return "\n".join(f"[{t.get('role')}]: {t.get('content') or ''}" for t in turns)


def build_snapshot(session_id: str, turns: list[dict]) -> str:
    """SPEC-001 deterministic snapshot."""
    counted = [t for t in turns if t.get("phase") != "_compression"]
    parts = [f"SNAPSHOT {session_id[:8]} (deterministic, verbatim from {len(counted)} stored turns)"]

    lines = decision_lines(turns)
    if lines:
        parts.append("DECISION_LINES:\n" + "\n".join(f"- {line}" for line in lines))
    else:
        parts.append("DECISION_LINES: none")

    refs = references(turns)
    parts.append("REFERENCES: " + (", ".join(refs) if refs else "none"))

    last = last_conversation_turns(turns)
    if last:
        parts.append("LAST_TURNS:\n" + _format_turns(last))
    else:
        parts.append("LAST_TURNS: none")
    return "\n".join(parts)


def build_prior_decisions_block(prior_session: dict | None, prior_turns: list[dict] | None) -> str:
    """SPEC-002 `prior_decisions` turn, from the prior session's stored turns."""
    if not prior_session:
        return "PRIOR_DECISIONS: unavailable (no closed session for this project)"
    label = str(prior_session.get("id") or "")[:8]
    lines = decision_lines(prior_turns or [])
    if not lines:
        return f"PRIOR_DECISIONS: none recorded in session {label}"
    header = (
        f"PRIOR_DECISIONS (historical record, verbatim from session {label} turns; "
        "not verified against the current repository):"
    )
    return header + "\n" + "\n".join(f"- {line}" for line in lines)


def build_prior_turns_block(prior_session: dict | None, prior_turns: list[dict] | None) -> str:
    """SPEC-002 `prior_turns` turn: the prior session's last two conversation turns."""
    if not prior_session:
        return "PRIOR_LAST_TURNS: unavailable (no closed session for this project)"
    label = str(prior_session.get("id") or "")[:8]
    last = last_conversation_turns(prior_turns or [])
    if not last:
        return f"PRIOR_LAST_TURNS: none recorded in session {label}"
    header = (
        f"PRIOR_LAST_TURNS (historical, verbatim from session {label}; "
        "not verified against the current repository):"
    )
    return header + "\n" + _format_turns(last)
