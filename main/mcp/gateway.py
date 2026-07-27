from __future__ import annotations

import json
from pathlib import Path

from .models import McpCapability, McpServerConfig
from .policy import McpPolicy
from .transport import InMemoryMcpTransport, McpTransport


class McpGateway:
    """Configuration-driven MCP boundary. Agent code receives only normalized capabilities."""
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".mcp" / "servers.json"; self.path.parent.mkdir(parents=True, exist_ok=True); self.policy = McpPolicy(); self._connected: set[str] = set(); self._servers = self.load(); self._transports: dict[str, McpTransport] = {}

    def load(self) -> dict[str, McpServerConfig]:
        if not self.path.exists(): return {}
        try: raw=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {}
        return {item["id"]: McpServerConfig(**item) for item in raw if isinstance(item, dict) and item.get("id")}

    def list_servers(self) -> list[dict]: return [{**server.to_dict(), "connected":server.id in self._connected} for server in self._servers.values()]
    def register_transport(self, server_id: str, transport: McpTransport) -> None:
        self._transports[server_id] = transport

    def connect(self, server_id: str) -> dict:
        server=self.require(server_id)
        if not server.enabled: raise ValueError("MCP server is disabled")
        transport = self._transports.get(server_id)
        if transport is None:
            if server.transport != "in_memory": raise NotImplementedError(f"MCP transport adapter is not configured: {server.transport}")
            transport = InMemoryMcpTransport(); self._transports[server_id] = transport
        transport.connect(server, timeout=server.timeout_seconds)
        self._connected.add(server_id); return {"server_id":server_id,"connected":True}
    def disconnect(self, server_id: str) -> dict:
        transport = self._transports.get(server_id)
        if transport is not None: transport.close()
        self._connected.discard(server_id); return {"server_id":server_id,"connected":False}
    def list_capabilities(self, mode: str) -> list[dict]:
        result=[]
        for server in self._servers.values():
            if self.policy.allowed(server, McpCapability(server.id,"", "", {}), mode):
                for capability in self.capabilities_for(server): result.append(capability.to_dict())
        return result
    def invoke(self, server_id: str, capability: str, arguments: dict, context: dict | None = None) -> dict:
        server=self.require(server_id)
        if server_id not in self._connected: raise RuntimeError("MCP server is not connected")
        item=next((candidate for candidate in self.capabilities_for(server) if candidate.name == capability), None)
        if item is None: raise FileNotFoundError("MCP capability not found")
        if self.policy.requires_confirmation(item) and not bool((context or {}).get("approved")): raise PermissionError("MCP capability requires user confirmation")
        transport = self._transports.get(server_id)
        if transport is None: raise RuntimeError("MCP server is not connected")
        result = transport.call_tool(capability, arguments, timeout=server.timeout_seconds, cancelled=(context or {}).get("token"))
        return {"status": "completed", "data": result, "audit": {"server_id": server_id, "capability": capability}}
    def health(self) -> dict: return {"servers":len(self._servers),"connected":len(self._connected),"status":"ok"}
    def require(self, server_id: str) -> McpServerConfig:
        if server_id not in self._servers: raise FileNotFoundError(f"MCP server not found: {server_id}")
        return self._servers[server_id]
    def capabilities_for(self, server: McpServerConfig) -> list[McpCapability]:
        # Config may safely publish static capability metadata; no remote execution occurs here.
        configured = list(server.capabilities or [])
        transport = self._transports.get(server.id)
        if transport is not None and server.id in self._connected:
            try: configured = transport.list_tools(timeout=server.timeout_seconds)
            except Exception: pass
        return [McpCapability(server.id, str(item.get("name") or ""), str(item.get("description") or ""), dict(item.get("input_schema") or item.get("inputSchema") or {}), str(item.get("risk_level") or server.trust_level)) for item in configured if isinstance(item, dict) and item.get("name")]
