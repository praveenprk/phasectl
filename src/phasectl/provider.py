"""LLM provider abstraction.

The current process talks to exactly one backend, chosen by
config.toml → [provider].backend. `AnthropicProvider` uses the anthropic
SDK (the historical default). `OpenAICompatProvider` uses raw httpx to
speak to any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama,
vLLM, LM Studio, llama.cpp server) — no SDK needed.

Both providers translate their native tool-call format into a common
internal shape that api.py consumes:

    tool_calls: list[{"id": str, "name": str, "arguments": dict}]
    tool_results (returned to provider.chat): list[{"id": str, "content": str}]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .auth import InvalidAPIKey, NoAPIKey, get_api_key


DEFAULT_COMPRESS_PROMPT = (
    "You are a session compression tool. "
    "Compress the following conversation turns into a concise summary "
    "preserving key decisions, open questions, and context."
)


class LLMProvider(ABC):
    @abstractmethod
    def chat(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, list[dict]]:
        """Return (text_response, tool_calls).

        `messages` uses this common shape:
          {"role": "user"|"assistant", "content": str} for plain turns
          {"role": "assistant", "content": str, "tool_calls": [...]}
          {"role": "tool", "tool_call_id": str, "content": str}

        Tools use Anthropic's `{name, description, input_schema}` shape;
        providers translate as needed internally.
        """

    @abstractmethod
    def compress(self, system: str, content: str, max_tokens: int) -> str:
        """Single-turn completion. Used for compression and synthesis."""


def _require_key() -> str:
    key = get_api_key()
    if not key:
        raise NoAPIKey()
    return key


def _provider_cfg(config: dict) -> dict:
    return config.get("provider", {}) or {}


class AnthropicProvider(LLMProvider):
    def __init__(self, config: dict):
        from anthropic import Anthropic
        p = _provider_cfg(config)
        self._model = p.get("model") or "claude-sonnet-4-6"
        self._compression_model = (
            p.get("compression_model") or "claude-haiku-4-5-20251001"
        )
        kwargs: dict[str, Any] = {"api_key": _require_key()}
        base = (p.get("api_base") or "").strip()
        if base:
            kwargs["base_url"] = base
        self._client = Anthropic(**kwargs)

    def chat(self, system, messages, tools, temperature, max_tokens):
        from anthropic import AuthenticationError, NOT_GIVEN

        native_messages = _to_anthropic_messages(messages)
        try:
            response = self._client.messages.create(
                model=self._model,
                system=system,
                messages=native_messages,
                tools=tools if tools else NOT_GIVEN,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except AuthenticationError:
            raise InvalidAPIKey()

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": dict(block.input) if block.input else {},
                })
        return "\n".join(text_parts), tool_calls

    def compress(self, system, content, max_tokens):
        from anthropic import AuthenticationError
        try:
            response = self._client.messages.create(
                model=self._compression_model,
                system=system,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=max_tokens,
            )
        except AuthenticationError:
            raise InvalidAPIKey()
        return response.content[0].text


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert common message shape into Anthropic's content-block form.

    Only messages with tool_calls / tool results need transforming; plain
    role+content pairs pass through.
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m.get("content", ""),
                }],
            })
            continue
        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls:
            blocks: list[dict] = []
            text = m.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc.get("arguments", {}) or {},
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": role, "content": m.get("content", "")})
    return out


class OpenAICompatProvider(LLMProvider):
    """Talks to any OpenAI-compatible /v1/chat/completions endpoint.

    Ollama's OpenAI-compatible API, OpenRouter, vLLM, LM Studio, and
    llama.cpp server all fit here. Uses httpx directly — no SDK.
    """

    _DEFAULT_BASES = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
    }

    _DEFAULT_MODELS = {
        "openai": ("gpt-4o", "gpt-4o-mini"),
        "openrouter": ("openai/gpt-4o", "openai/gpt-4o-mini"),
    }

    def __init__(self, config: dict):
        import httpx
        p = _provider_cfg(config)
        backend = (p.get("backend") or "openai").strip()
        base = (p.get("api_base") or "").strip()
        if not base:
            base = self._DEFAULT_BASES.get(backend, "")
        if not base:
            raise RuntimeError(
                f"provider backend '{backend}' has no default api_base; "
                "set provider.api_base in config.toml"
            )
        self._base = base.rstrip("/")
        model = (p.get("model") or "").strip()
        compression_model = (p.get("compression_model") or "").strip()
        if not model:
            default_chat, _ = self._DEFAULT_MODELS.get(backend, ("", ""))
            model = default_chat
        if not compression_model:
            _, default_compress = self._DEFAULT_MODELS.get(backend, ("", ""))
            compression_model = default_compress or model
        if not model:
            raise RuntimeError(
                f"provider backend '{backend}' has no default model; "
                "set provider.model in config.toml (or pass --model)"
            )
        self._model = model
        self._compression_model = compression_model
        # ollama does not require a key; others do
        key = get_api_key()
        if not key and backend != "ollama":
            raise NoAPIKey()
        self._headers = {"Content-Type": "application/json"}
        if key:
            self._headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(timeout=120.0)

    def _post(self, payload: dict) -> dict:
        import httpx
        url = f"{self._base}/chat/completions"
        try:
            resp = self._client.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as e:
            raise RuntimeError(f"HTTP error calling {url}: {e}")
        if resp.status_code == 401:
            raise InvalidAPIKey()
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{url} → {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    def chat(self, system, messages, tools, temperature, max_tokens):
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _to_openai_messages(system, messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [_tool_to_openai(t) for t in tools]
        data = self._post(payload)

        choices = data.get("choices") or []
        if not choices:
            return "", []
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        tool_calls: list[dict] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments", "{}")
            try:
                import json
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (ValueError, TypeError):
                args = {}
            tool_calls.append({
                "id": tc.get("id") or fn.get("name", ""),
                "name": fn.get("name", ""),
                "arguments": args or {},
            })
        return text, tool_calls

    def compress(self, system, content, max_tokens):
        payload = {
            "model": self._compression_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        data = self._post(payload)
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "") or ""


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    import json
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m.get("content", ""),
            })
            continue
        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls:
            out.append({
                "role": "assistant",
                "content": m.get("content", "") or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {}) or {}),
                        },
                    }
                    for tc in tool_calls
                ],
            })
            continue
        out.append({"role": role, "content": m.get("content", "")})
    return out


def _tool_to_openai(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def get_provider(config: dict) -> LLMProvider:
    backend = (_provider_cfg(config).get("backend") or "anthropic").strip()
    if backend == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatProvider(config)
