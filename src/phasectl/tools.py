import os
from typing import Any

from .mcp_client import MCPClient, MCPNotAvailableError


ACTION_SCHEMAS = {
    "search_symbols": {
        "description": "Search for symbols matching a query across the pre-configured, already-indexed repository. Repo is injected automatically — do not ask for it.",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query string"},
            "kind": {"type": "string", "enum": ["function", "class", "method", "variable", "type"], "description": "Symbol kind filter"},
            "limit": {"type": "integer", "default": 20, "description": "Max results"},
        }
    },
    "get_symbol_source": {
        "description": "Get full source of one symbol (symbol_id -> flat object). Repo is auto-injected.",
        "required": ["symbol_id"],
        "properties": {
            "symbol_id": {"type": "string", "description": "Symbol ID (e.g., 'src/app.py::MyClass#class' or 'src/app.py::my_func#function')"},
        }
    },
    "get_file_outline": {
        "description": "Get all symbols (functions, classes, methods) in a file with signatures. Repo is auto-injected.",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "description": "Path to file relative to project root"},
        }
    },
    "get_blast_radius": {
        "description": "Find all files affected by changing a symbol. Repo is auto-injected.",
        "required": ["symbol"],
        "properties": {
            "symbol": {"type": "string", "description": "Symbol name or identifier"},
            "depth": {"type": "integer", "default": 2, "description": "Call graph depth"},
        }
    },
    "get_ranked_context": {
        "description": "Assemble best-fit context for a query within a token budget. Repo is auto-injected.",
        "required": ["query", "token_budget"],
        "properties": {
            "query": {"type": "string", "description": "Task or query description"},
            "token_budget": {"type": "integer", "description": "Max tokens for context bundle"},
            "compress": {"type": "boolean", "default": False, "description": "Compress results to fit budget"},
        }
    },
    "get_untested_symbols": {
        "description": "Find functions and methods with no evidence of being exercised by any test file. Repo is auto-injected.",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "description": "File path to analyze"},
            "project_root": {"type": "string", "description": "Project root for test discovery"},
        }
    },
    "index_folder": {
        "description": "Index a local folder of source code.",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "Absolute path to project root"},
        }
    },
    "get_call_hierarchy": {
        "description": "Return incoming callers and outgoing callees for a symbol, N levels deep. Repo is auto-injected.",
        "required": ["symbol_id"],
        "properties": {
            "symbol_id": {"type": "string", "description": "Symbol ID (e.g., 'src/app.py::main#function')"},
            "depth": {"type": "integer", "default": 2, "description": "Call graph depth"},
        }
    },
}


PHASE_TOOLS = {
    "impl": ["search_symbols", "get_symbol_source", "get_file_outline", "get_ranked_context", "get_blast_radius"],
    "validate": ["search_symbols", "get_symbol_source", "get_untested_symbols"],
}


ACTION_TO_PHASE_TOOL = {
    "search_symbols": "search_symbols",
    "get_symbol_source": "get_symbol_source",
    "get_file_outline": "get_file_outline",
    "get_ranked_context": "get_ranked_context",
    "get_blast_radius": "get_blast_radius",
    "get_untested_symbols": "get_untested_symbols",
    "index_project": "index_folder",
    "get_call_hierarchy": "get_call_hierarchy",
}


def get_phase_tools(phase: str) -> list[dict]:
    """Expose one Anthropic tool per allowed jCodeMunch action.

    Each tool's name equals the action name and its input_schema mirrors the
    action's own schema (repo omitted — auto-injected in ToolExecutor.execute).
    This matches the arg shape the working `phasectl query` path passes to
    execute(), so both paths dispatch through jCodeMunch's `order` front door
    identically.
    """
    tool_names = PHASE_TOOLS.get(phase, [])
    tools = []
    for name in tool_names:
        schema = ACTION_SCHEMAS.get(name)
        if not schema:
            continue
        tools.append({
            "name": name,
            "description": schema["description"],
            "input_schema": {
                "type": "object",
                "properties": schema["properties"],
                "required": schema["required"],
            },
        })
    return tools


class ToolExecutor:
    def __init__(self, project: str = ""):
        self._client: MCPClient | None = None
        self.project = project
        self._repo_map = {
            "contextos": "praveenprk/contextos",
        }

    def _get_client(self) -> MCPClient | None:
        if self._client is None:
            try:
                self._client = MCPClient(["jcodemunch-mcp", "serve"])
                self._client.__enter__()
            except Exception:
                return None
        return self._client

    def _resolve_repo(self, project: str) -> str:
        return self._repo_map.get(project, project)

    def execute(self, name: str, arguments: dict) -> str:
        action = ACTION_TO_PHASE_TOOL.get(name, name)
        repo = self._resolve_repo(self.project) if self.project else arguments.get("repo")
        
        if action == "index_folder":
            path = arguments.get("path") or arguments.get("project_path")
            if not path:
                return "[jCodeMunch error: index_folder requires path argument]"
            order_args = {"path": path}
            allow_state_change = True
        else:
            if repo:
                arguments["repo"] = repo
            order_args = arguments
            allow_state_change = False

        client = self._get_client()
        if client is None:
            return "[jCodeMunch unavailable — answer from context only]"
        try:
            return client.call_tool("order", {"action": action, "args": order_args, "allow_state_change": allow_state_change})
        except MCPNotAvailableError:
            return "[jCodeMunch unavailable — answer from context only]"
        except Exception as e:
            return f"[jCodeMunch error: {e}]"

    def close(self):
        if self._client:
            self._client.__exit__(None, None, None)
            self._client = None