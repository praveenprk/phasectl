from .provider import DEFAULT_COMPRESS_PROMPT, get_provider
from .tools import ToolExecutor
from .graph import GraphStore
from .config import get_graph_path


def chat(
    messages: list[dict],
    phase_config,
    app_config: dict,
    project: str = "",
    on_tool_call=None,
    on_round=None,
) -> tuple[str, int]:
    """Run the chat loop; returns (final_text, num_tools_available).

    `on_tool_call(name, arguments)` fires immediately before each MCP
    tool invocation. `on_round(index)` fires before each provider call
    (index starts at 0 for the first request).
    """
    provider = get_provider(app_config)
    tool_executor = ToolExecutor(project=project)
    tools = tool_executor.get_tools_for_phase(phase_config.name, app_config)
    tools_available = len(tools)

    try:
        round_ix = 0
        while True:
            if on_round is not None:
                on_round(round_ix, tools_available)
            text, tool_calls = provider.chat(
                system=phase_config.system_prompt,
                messages=messages,
                tools=tools if tools else None,
                temperature=phase_config.temperature,
                max_tokens=4096,
            )
            round_ix += 1

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    if on_tool_call is not None:
                        try:
                            on_tool_call(tc.get("name", ""), tc.get("arguments") or {})
                        except Exception:
                            pass
                    result = tool_executor.execute(
                        tc["name"], tc["arguments"], config=app_config
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                continue

            return text, tools_available
    finally:
        tool_executor.close()


def compress(turns_text: str, app_config: dict, system_prompt: str | None = None) -> str:
    provider = get_provider(app_config)
    return provider.compress(
        system=system_prompt or DEFAULT_COMPRESS_PROMPT,
        content=turns_text,
        max_tokens=1024,
    )


def extract_rfcs_and_decisions(summary: str) -> tuple[list[str], list[str]]:
    import re
    rfcs = [f"RFC-{m}" for m in re.findall(r"RFC-(\d+)", summary)]
    decisions = [f"DECISION-{m}" for m in re.findall(r"DECISION-(\d+)", summary)]
    return rfcs, decisions


def persist_session_graph(
    session_id: str,
    project: str,
    summary: str,
    created_at: str,
    app_config: dict,
) -> None:
    store = _get_graph_store(app_config)
    if not store:
        return

    store.save_session_node(session_id, project, created_at)

    rfcs, decisions = extract_rfcs_and_decisions(summary)
    for rfc_id in rfcs:
        store.save_rfc(rfc_id, project, "in-progress", "")
        store.link_session_rfc(session_id, rfc_id)

    for decision_id in decisions:
        store.save_decision(decision_id, project, "")
        store.link_session_decision(session_id, decision_id)

    store.close()


def _get_graph_store(app_config: dict) -> GraphStore | None:
    try:
        return GraphStore(str(get_graph_path(app_config)))
    except Exception:
        return None
