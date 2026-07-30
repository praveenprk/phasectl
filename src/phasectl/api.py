import os
from typing import NotRequired, TypedDict

from anthropic import Anthropic
from anthropic import NOT_GIVEN

from .tools import ToolExecutor, get_phase_tools
from .graph import GraphStore
from .config import get_graph_path


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        _client = Anthropic(api_key=api_key)
    return _client


class ToolResult(TypedDict):
    type: str
    tool_use_id: str
    content: str


def chat(
    messages: list[dict],
    phase_config,
    app_config: dict,
    project: str = "",
) -> str:
    client = _get_client()
    model = app_config["api"]["model"]
    tools = get_phase_tools(phase_config.name)

    tool_executor = ToolExecutor(project=project)

    while True:
        response = client.messages.create(
            model=model,
            system=phase_config.system_prompt,
            messages=messages,
            tools=tools if tools else NOT_GIVEN,
            temperature=phase_config.temperature,
            max_tokens=4096,
        )

        if response.stop_reason == "tool_use":
            tool_results: list[ToolResult] = []
            assistant_content = []

            for block in response.content:
                if block.type == "tool_use":
                    result = tool_executor.execute(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                assistant_content.append(block)

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            continue

        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        return "\n".join(text_parts)


def compress(turns_text: str, app_config: dict, system_prompt: str | None = None) -> str:
    client = _get_client()
    model = app_config["api"]["compression_model"]

    if system_prompt is None:
        system_prompt = (
            "You are a session compression tool. "
            "Compress the following conversation turns into a concise summary "
            "preserving key decisions, open questions, and context."
        )

    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=[{"role": "user", "content": turns_text}],
        temperature=0.3,
        max_tokens=1024,
    )
    return response.content[0].text


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


def _get_graph_store(config: dict) -> GraphStore | None:
    try:
        return GraphStore(str(get_graph_path(config)))
    except Exception:
        return None