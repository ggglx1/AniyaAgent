from __future__ import annotations

import json
from pathlib import Path

from .models import McpCapability, McpServerConfig
from .policy import McpPolicy


class McpGateway:
    """Configuration-driven MCP boundary. Agent code receives only normalized capabilities."""
    def __init__(self, workdir: Path):
        self.path = workdir.resolve() / ".mcp" / "servers.json"; self.path.parent.mkdir(parents=True, exist_ok=True); self.policy = McpPolicy(); self._connected: set[str] = set(); self._servers = self.load()

    def load(self) -> dict[str, McpServerConfig]:
        if not self.path.exists(): return {}
        try: raw=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {}
        return {item["id"]: McpServerConfig(**item) for item in raw if isinstance(item, dict) and item.get("id")}

    def list_servers(self) -> list[dict]: return [{**server.to_dict(), "connected":server.id in self._connected} for server in self._servers.values()]
    def connect(self, server_id: str) -> dict:
        server=self.require(server_id)
        if not server.enabled: raise ValueError("MCP server is disabled")
        self._connected.add(server_id); return {"server_id":server_id,"connected":True}
    def disconnect(self, server_id: str) -> dict: self._connected.discard(server_id); return {"server_id":server_id,"connected":False}
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
        # Transport adapters intentionally remain external; never fake a remote side effect.
        raise NotImplementedError(f"MCP transport '{server.transport}' invocation adapter is not configured")
    def health(self) -> dict: return {"servers":len(self._servers),"connected":len(self._connected),"status":"ok"}
    def require(self, server_id: str) -> McpServerConfig:
        if server_id not in self._servers: raise FileNotFoundError(f"MCP server not found: {server_id}")
        return self._servers[server_id]
    def capabilities_for(self, server: McpServerConfig) -> list[McpCapability]:
        # Config may safely publish static capability metadata; no remote execution occurs here.
        return []
