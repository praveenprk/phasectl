"""Plain-binary code indexer dispatch.

phasectl shells out directly to the configured indexer binary — no MCP,
no JSON-RPC handshake, no dynamic tool discovery. There is exactly one
tool, `search_symbols`, which runs `<indexer> query <term> --path
<index_path>` as a subprocess and returns its stdout verbatim. Default
indexer is phasedex (docs/internal/NATIVE_INDEXER_PLAN.md), phasectl's
own native C++ indexer — no Node, no npm, no interpreter runtime.
CodeGraph (github.com/colbymchenry/codegraph) remains a supported swap:
`codegraph query <search> [--path <dir>] [--json]` — no `--repo`-style
scoping, and `--path` is required to target a project from outside its
directory, which is the common case for phasectl.

    [tools]
    indexer_command = "phasedex"   # default; change to swap indexers
"""

from __future__ import annotations

import shlex
import shutil
import subprocess


_DEFAULT_INDEXER_COMMAND = "phasedex"

_SEARCH_SYMBOLS_TOOL = {
    "name": "search_symbols",
    "description": "Search the project's indexed symbols (functions, classes, routes) by name or pattern.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Symbol name or pattern to search for."},
        },
        "required": ["query"],
    },
}


def _indexer_command(config: dict) -> list[str]:
    raw = ((config.get("tools") or {}).get("indexer_command") or "").strip()
    if not raw:
        raw = _DEFAULT_INDEXER_COMMAND
    return shlex.split(raw)


class ToolExecutor:
    """Runs the configured indexer binary directly for symbol search."""

    def __init__(self, project: str = "", indexer_command: str = ""):
        self.project = project
        self._indexer_command_override = indexer_command

    def _resolved_command(self, config: dict | None = None) -> list[str]:
        if self._indexer_command_override:
            return shlex.split(self._indexer_command_override)
        return _indexer_command(config or {})

    def _project_index_path(self, config: dict | None = None) -> str | None:
        if not self.project:
            return None
        from .config import get_index_path
        path = get_index_path(self.project, config or {})
        return str(path) if path else None

    def discovered_tools(self, config: dict | None = None) -> list[dict]:
        """Return the single fixed tool, if the indexer binary is on PATH."""
        command = self._resolved_command(config)
        if not command or shutil.which(command[0]) is None:
            return []
        return [dict(_SEARCH_SYMBOLS_TOOL)]

    def get_tools_for_phase(self, phase: str, config: dict | None = None) -> list[dict]:
        """Return tool definitions (Anthropic/OpenAI-shape) for the given phase."""
        if phase not in ("impl", "validate"):
            return []
        return self.discovered_tools(config)

    def execute(self, name: str, arguments: dict, config: dict | None = None) -> str:
        """Run the indexer binary's `query` subcommand with the given arguments."""
        command = self._resolved_command(config)
        if not command or shutil.which(command[0]) is None:
            return "[indexer unavailable — answer from context only]"

        args = arguments or {}
        argv = [*command, "query", str(args.get("query", ""))]

        index_path = self._project_index_path(config)
        if index_path:
            argv += ["--path", index_path]
        if args.get("json"):
            argv.append("--json")

        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            return "[indexer timed out]"
        except Exception as e:
            return f"[indexer error: {e}]"

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip().splitlines()
            hint = stderr_tail[-1] if stderr_tail else str(proc.returncode)
            return f"[indexer error: {hint}]"

        return (proc.stdout or "").strip() or "[no matches]"

    def close(self) -> None:
        pass
