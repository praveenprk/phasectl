from .provider import DEFAULT_COMPRESS_PROMPT, get_provider
from .tools import ToolExecutor, get_phase_tools
from .graph import GraphStore
from .config import get_graph_path


def chat(
    messages: list[dict],
    phase_config,
    app_config: dict,
    project: str = "",
) -> str:
    provider = get_provider(app_config)
    tool_executor = ToolExecutor(project=project)
    tools = get_phase_tools(phase_config.name)

    try:
        while True:
            text, tool_calls = provider.chat(
                system=phase_config.system_prompt,
                messages=messages,
                tools=tools if tools else None,
                temperature=phase_config.temperature,
                max_tokens=4096,
            )

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    result = tool_executor.execute(tc["name"], tc["arguments"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                continue

            return text
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
