"""MCP tool discovery and dispatch.

At connect time we call `tools/list` and expose whatever the server
offers — no hardcoded tool names, no assumed schemas, no wrapping. The
chat loop hands Claude the exact tool definitions the server surfaced;
`execute(name, args)` is a straight passthrough to `client.call_tool`.

Phase filtering is heuristic by default (impl gets the full surface;
validate drops tools whose name/description read as mutations). Users
can override per phase with:

    [tools]
    mcp_command = "jcodemunch-mcp serve"
    impl_tools     = ["order", "menu", "route", "search_symbols"]
    validate_tools = ["order", "menu"]
"""

from __future__ import annotations

import shlex
from typing import Any

from .mcp_client import MCPClient, MCPNotAvailableError


_DEFAULT_MCP_COMMAND = "jcodemunch-mcp serve"

_MUTATION_MARKERS = (
    "write", "create", "delete", "update", "mutate", "remove", "modify",
    "index_repo", "index_folder", "set_", "reset", "clear", "commit",
    "push", "publish", "install", "rebuild",
)


def _is_mutation_tool(tool: dict) -> bool:
    """Heuristic: does this tool look like it changes state? Conservative."""
    hay = (tool.get("name", "") + " " + (tool.get("description") or "")).lower()
    return any(marker in hay for marker in _MUTATION_MARKERS)


def _mcp_command(config: dict) -> list[str]:
    raw = ((config.get("tools") or {}).get("mcp_command") or "").strip()
    if not raw:
        raw = _DEFAULT_MCP_COMMAND
    return shlex.split(raw)


class ToolExecutor:
    def __init__(self, project: str = "", mcp_command: str = ""):
        self._client: MCPClient | None = None
        self.project = project
        self._mcp_command_override = mcp_command
        self._server_tools: list[dict] | None = None

    def _resolved_command(self, config: dict | None = None) -> list[str]:
        if self._mcp_command_override:
            return shlex.split(self._mcp_command_override)
        return _mcp_command(config or {})

    def _get_client(self, config: dict | None = None) -> MCPClient | None:
        if self._client is None:
            try:
                self._client = MCPClient(self._resolved_command(config))
                self._client.__enter__()
                try:
                    self._server_tools = self._client.list_tools()
                except Exception:
                    self._server_tools = []
            except Exception:
                self._client = None
                return None
        return self._client

    def discovered_tools(self, config: dict | None = None) -> list[dict]:
        """Return the raw tool list from the MCP server (best-effort)."""
        self._get_client(config)
        return list(self._server_tools or [])

    def get_tools_for_phase(self, phase: str, config: dict | None = None) -> list[dict]:
        """Return tool definitions (Anthropic/OpenAI-shape) for the given phase."""
        if phase not in ("impl", "validate"):
            return []

        client = self._get_client(config)
        if not client or not self._server_tools:
            return []

        cfg = (config or {}).get("tools") or {}
        allowlist_key = f"{phase}_tools"
        allowlist = cfg.get(allowlist_key)

        if isinstance(allowlist, list) and allowlist:
            wanted = set(allowlist)
            selected = [t for t in self._server_tools if t.get("name") in wanted]
        else:
            selected = list(self._server_tools)
            if phase == "validate":
                selected = [t for t in selected if not _is_mutation_tool(t)]

        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
            }
            for t in selected
            if t.get("name")
        ]

    def execute(self, name: str, arguments: dict, config: dict | None = None) -> str:
        """Call a discovered MCP tool by name with the given arguments."""
        client = self._get_client(config)
        if client is None:
            return "[MCP server unavailable — answer from context only]"
        try:
            return client.call_tool(name, arguments or {})
        except MCPNotAvailableError:
            return "[MCP server unavailable — answer from context only]"
        except Exception as e:
            return f"[MCP error: {e}]"

    def close(self):
        if self._client:
            try:
                self._client.__exit__(None, None, None)
            except Exception:
                pass
            self._client = None
