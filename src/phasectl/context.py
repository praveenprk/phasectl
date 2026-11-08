from .api import compress as api_compress
from .graph import _visible_prose, extract_decision_references


_MAX_DECISION_LINE_CHARS = 300


def estimate_tokens(content: str) -> int:
    return len(content) // 4


def should_compress(turns: list[dict], token_budget: int) -> bool:
    uncompressed = [t for t in turns if not t["compressed"]]
    total = sum(t["token_estimate"] for t in uncompressed)
    return len(uncompressed) >= 3 and total > token_budget


def compress_tail(session_id: str, store, app_config: dict) -> str:
    turns = store.get_uncompressed_turns(session_id)
    if len(turns) <= 2:
        return ""

    compressable = turns[:-2]
    turn_texts = [
        f"[{t['phase']}][{t['role']}]: {t['content']}" for t in compressable
    ]
    turns_text = "\n\n".join(turn_texts)

    summary = api_compress(turns_text, app_config)

    turn_ids = [t["id"] for t in compressable]
    store.mark_turns_compressed(turn_ids)

    summary_content = f"[COMPRESSED SUMMARY: {summary}]"
    store.add_turn(
        session_id=session_id,
        phase="_compression",
        role="system",
        content=summary_content,
        token_estimate=estimate_tokens(summary_content),
        origin="compression",
    )
    return summary


def build_prior_decisions_block(prior_session: dict | None) -> str:
    """CLI-owned decision handoff from the last closed snapshot.

    Deterministic: carries only the snapshot lines that contain decision
    references, verbatim (bullet markers stripped), in first-occurrence order.
    No model call. Explicit when there is nothing to carry, so the model is
    never left to fill the gap.
    """
    if not prior_session:
        return "PRIOR_DECISIONS: unavailable (no closed session for this project)"

    session_label = str(prior_session.get("id") or "")[:8]
    summary = prior_session.get("final_summary") or ""
    references = extract_decision_references(summary)
    if not references:
        return f"PRIOR_DECISIONS: none recorded in last session snapshot ({session_label})"

    lines: list[str] = []
    for reference in references:
        for raw in _visible_prose(summary).splitlines():
            if reference not in extract_decision_references(raw):
                continue
            line = raw.strip().lstrip("-*•").strip()
            if len(line) > _MAX_DECISION_LINE_CHARS:
                line = line[: _MAX_DECISION_LINE_CHARS - 3] + "..."
            if line not in lines:
                lines.append(line)
            break
        else:
            lines.append(reference)

    header = (
        f"PRIOR_DECISIONS (historical record, verbatim from last session "
        f"snapshot {session_label}; not verified against the current repository):"
    )
    return header + "\n" + "\n".join(f"- {line}" for line in lines)


def build_injection_block(prior_session: dict | None, prior_turns: list[dict] | None) -> str:
    parts = []
    if prior_session:
        summary = prior_session.get("final_summary")
        if summary:
            parts.append(f"Prior session summary:\n{summary}")
    if prior_turns:
        turn_lines = [f"[{t['role']}]: {t['content']}" for t in prior_turns]
        parts.append("Last turns from prior session:\n" + "\n".join(turn_lines))
    return "\n\n".join(parts)
