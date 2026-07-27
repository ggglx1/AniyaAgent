from __future__ import annotations

from typing import Protocol


class McpTransport(Protocol):
    def connect(self, config, *, timeout: float = 30) -> None: ...
    def list_tools(self, *, timeout: float = 30) -> list[dict]: ...
    def call_tool(self, name: str, arguments: dict, *, timeout: float = 30, cancelled=None) -> dict: ...
    def close(self) -> None: ...


class InMemoryMcpTransport:
    """Working transport for embedded/test MCP adapters; external transports are injectable."""
    def __init__(self, tools: dict[str, object] | None = None): self.tools = tools or {}; self.connected = False
    def connect(self, config, *, timeout: float = 30) -> None: self.connected = True
    def list_tools(self, *, timeout: float = 30) -> list[dict]:
        return [value if isinstance(value, dict) else {"name": name, "description": "Embedded MCP capability", "inputSchema": {"type": "object", "properties": {}}} for name, value in self.tools.items()]
    def call_tool(self, name: str, arguments: dict, *, timeout: float = 30, cancelled=None) -> dict:
        if cancelled is not None: cancelled.check()
        tool = self.tools.get(name)
        if tool is None: raise FileNotFoundError(f"MCP capability not found: {name}")
        if not callable(tool): raise TypeError(f"MCP capability {name} is metadata only")
        value = tool(arguments)
        if cancelled is not None: cancelled.check()
        return value if isinstance(value, dict) else {"content": value}
    def close(self) -> None: self.connected = False
