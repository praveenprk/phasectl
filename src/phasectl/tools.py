from typing import Any
from .mcp_client import MCPClient, MCPNotAvailableError, MCPTimeoutError


JCODEMUNCH_TOOLS = [
    {
        "name": "search_symbols",
        "description": "Search for symbols by name or pattern. Returns matching symbols with file, kind, and signature.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol name or pattern to search for"},
                "project": {"type": "string", "description": "Project name"},
                "limit": {"type": "integer", "default": 20, "description": "Max results"},
            },
            "required": ["query", "project"],
        },
    },
    {
        "name": "get_symbol_source",
        "description": "Get full source code of a symbol by its qualified name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Qualified symbol name (e.g., 'agent_node.AgentNode.set_identity')"},
                "project": {"type": "string", "description": "Project name"},
            },
            "required": ["symbol_name", "project"],
        },
    },
    {
        "name": "get_callers",
        "description": "Find all callers of a function/method.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Qualified symbol name"},
                "project": {"type": "string", "description": "Project name"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["symbol_name", "project"],
        },
    },
    {
        "name": "get_blast_radius",
        "description": "Analyze impact of changing a symbol - finds downstream dependents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "Qualified symbol name"},
                "project": {"type": "string", "description": "Project name"},
                "depth": {"type": "integer", "default": 3},
            },
            "required": ["symbol_name", "project"],
        },
    },
    {
        "name": "get_untested_symbols",
        "description": "Find untested or undertested symbols in a file or module.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path relative to project root"},
                "project": {"type": "string", "description": "Project name"},
            },
            "required": ["file_path", "project"],
        },
    },
    {
        "name": "index_project",
        "description": "Index a project directory for symbol search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name"},
                "path": {"type": "string", "description": "Absolute path to project root"},
            },
            "required": ["project", "path"],
        },
    },
]


PHASE_TOOLS = {
    "orient": [],
    "ideate": [],
    "design": [],
    "impl": [
        "search_symbols",
        "get_symbol_source",
        "get_callers",
        "get_blast_radius",
    ],
    "validate": [
        "search_symbols",
        "get_symbol_source",
        "get_untested_symbols",
    ],
    "snapshot": [],
}


def get_phase_tools(phase: str) -> list[dict]:
    tool_names = PHASE_TOOLS.get(phase, [])
    return [t for t in JCODEMUNCH_TOOLS if t["name"] in tool_names]


class ToolExecutor:
    def __init__(self, mcp_command: list[str] = None):
        self.mcp_command = mcp_command or ["jcodemunch-mcp", "stdio"]
        self._client: MCPClient | None = None

    def _ensure_client(self) -> MCPClient:
        if self._client is None:
            self._client = MCPClient(self.mcp_command)
            self._client.__enter__()
            self._client.initialize()
        return self._client

    def execute(self, tool_name: str, arguments: dict) -> str:
        try:
            client = self._ensure_client()
            return client.call_tool(tool_name, arguments)
        except MCPNotAvailableError:
            return f"[ERROR] jCodeMunch MCP not available. Is 'jcodemunch-mcp' installed and in PATH?"
        except MCPTimeoutError:
            return f"[ERROR] jCodeMunch request timed out"
        except Exception as e:
            return f"[ERROR] Tool execution failed: {e}"

    def close(self) -> None:
        if self._client:
            self._client.__exit__(None, None, None)
            self._client = None