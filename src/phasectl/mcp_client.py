import json
import subprocess
import time
from typing import Any


class MCPNotAvailableError(Exception):
    pass


class MCPTimeoutError(Exception):
    pass


class MCPClient:
    def __init__(self, command: list[str]):
        self.command = command
        self._proc: subprocess.Popen | None = None
        self._request_id = 0

    def __enter__(self) -> "MCPClient":
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            raise MCPNotAvailableError(f"Command not found: {self.command[0]}")
        return self

    def __exit__(self, *_) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def _send(self, method: str, params: dict | None = None) -> dict:
        if not self._proc or self._proc.poll() is not None:
            raise MCPNotAvailableError("MCP process not running")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()

        start = time.time()
        while time.time() - start < 10:
            line = self._proc.stdout.readline()
            if not line:
                time.sleep(0.01)
                continue
            try:
                response = json.loads(line)
                if response.get("id") == self._request_id:
                    if "error" in response:
                        raise MCPNotAvailableError(response["error"].get("message", "MCP error"))
                    return response.get("result", {})
            except json.JSONDecodeError:
                continue
        raise MCPTimeoutError("MCP request timed out after 10s")

    def initialize(self) -> dict:
        return self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "phasectl", "version": "0.1.0"},
        })

    def list_tools(self) -> list[dict]:
        return self._send("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text_parts = []
            for item in content:
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return "\n".join(text_parts)
        return str(result)