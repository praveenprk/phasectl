import os
from typing import Any

from anthropic import Anthropic
from anthropic import NOT_GIVEN

from .tools import ToolExecutor, get_phase_tools


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        _client = Anthropic(api_key=api_key)
    return _client


def _extract_text(content: list) -> str:
    parts = []
    for block in content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts)


def _format_tool_results(tool_results: list[dict]) -> list[dict]:
    formatted = []
    for tr in tool_results:
        formatted.append({
            "type": "tool_result",
            "tool_use_id": tr["tool_use_id"],
            "content": tr["content"],
        })
    return formatted


def chat(
    messages: list[dict],
    system_prompt: str,
    temperature: float,
    app_config: dict,
    phase: str = "impl",
) -> str:
    client = _get_client()
    model = app_config["api"]["model"]

    tools = get_phase_tools(phase)
    tool_executor = ToolExecutor()

    try:
        while True:
            response = client.messages.create(
                model=model,
                system=system_prompt,
                messages=messages,
                tools=tools if tools else NOT_GIVEN,
                temperature=temperature,
                max_tokens=4096,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = tool_executor.execute(block.name, block.input)
                        tool_results.append({
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": _format_tool_results(tool_results)})
                continue

            return _extract_text(response.content)
    finally:
        tool_executor.close()


def compress(
    turns_text: str, app_config: dict, system_prompt: str | None = None
) -> str:
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