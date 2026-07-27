import os

from anthropic import Anthropic


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        _client = Anthropic(api_key=api_key)
    return _client


def chat(
    messages: list[dict],
    system_prompt: str,
    temperature: float,
    app_config: dict,
) -> str:
    client = _get_client()
    model = app_config["api"]["model"]

    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=messages,
        temperature=temperature,
        max_tokens=4096,
    )
    return response.content[0].text


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
