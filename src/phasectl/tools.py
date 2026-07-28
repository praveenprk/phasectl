from typing import Any

from .mcp_client import MCPClient, MCPNotAvailableError


JCODEMUNCH_TOOLS = [
    {
        "name": "search_symbols",
        "description": "Find symbols by name pattern, kind, or file path. Returns matching symbols with name, kind, file, and signature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Symbol name pattern (supports wildcards)"},
                "kind": {"type": "string", "enum": ["function", "class", "method", "variable", "type"], "description": "Symbol kind filter"},
                "file_pattern": {"type": "string", "description": "File path pattern filter"},
                "limit": {"type": "integer", "default": 20, "description": "Max results"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "get_symbol_source",
        "description": "Get exact source code for a symbol at byte precision. Returns full function/class definition with body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Fully qualified symbol name (e.g., 'module.Class.method')"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_file_outline",
        "description": "Get file structure: imports, classes, functions with signatures only. No bodies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to file relative to project root"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "assemble_task_context",
        "description": "Natural language task -> token-budgeted context bundle. Returns relevant symbols, files, and relationships.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description in natural language"},
                "token_budget": {"type": "integer", "default": 8000, "description": "Max tokens for context bundle"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "get_blast_radius",
        "description": "Find all callers and dependents of a symbol. What breaks if this changes?",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Fully qualified symbol name"},
                "depth": {"type": "integer", "default": 2, "description": "Call graph depth"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_untested_symbols",
        "description": "Find symbols in a file/module with no test reachability. Returns gaps for test planning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path to analyze"},
                "project_root": {"type": "string", "description": "Project root for test discovery"},
            },
            "required": ["file_path"],
        },
    },
]


PHASE_TOOLS = {
    "impl": ["search_symbols", "get_symbol_source", "get_file_outline", "assemble_task_context", "get_blast_radius"],
    "validate": ["search_symbols", "get_symbol_source", "get_untested_symbols"],
}


def get_phase_tools(phase: str) -> list[dict]:
    tool_names = PHASE_TOOLS.get(phase, [])
    return [t for t in JCODEMUNCH_TOOLS if t["name"] in tool_names]


class ToolExecutor:
    def __init__(self):
        self._client: MCPClient | None = None

    def _get_client(self) -> MCPClient | None:
        if self._client is None:
            try:
                self._client = MCPClient(["jcodemunch-mcp"])
                self._client.__enter__()
            except Exception:
                return None
        return self._client

    def execute(self, name: str, arguments: dict) -> str:
        client = self._get_client()
        if client is None:
            return "[jCodeMunch unavailable — answer from context only]"
        try:
            return client.call_tool(name, arguments)
        except MCPNotAvailableError:
            return "[jCodeMunch unavailable — answer from context only]"
        except Exception as e:
            return f"[jCodeMunch error: {e}]"

    def close(self):
        if self._client:
            self._client.__exit__(None, None, None)
            self._client = None