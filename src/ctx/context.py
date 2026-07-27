from .api import compress as api_compress


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
    )
    return summary


def build_injection_block(prior_session: dict, prior_turns: list[dict]) -> str:
    parts = []
    summary = prior_session.get("final_summary")
    if summary:
        parts.append(f"Prior session summary:\n{summary}")
    if prior_turns:
        turn_lines = [f"[{t['role']}]: {t['content']}" for t in prior_turns]
        parts.append("Last turns from prior session:\n" + "\n".join(turn_lines))
    return "\n\n".join(parts)
