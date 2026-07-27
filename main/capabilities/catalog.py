from __future__ import annotations

from .models import Capability


class CapabilityCatalog:
    """Describes capabilities without forcing every tool into every model prompt."""
    def __init__(self, application): self.app = application

    def select(self, required: list[str], mode: str) -> list[Capability]:
        if mode in {"direct_conversation", "qa", "structured_action", "proactive"}: return []
        results: list[Capability] = []
        if "local_tools" in required:
            tools = getattr(self.app.runtime, "tools", None)
            for definition in list(getattr(tools, "definitions", []) or []):
                results.append(Capability(f"local:{definition['name']}", "local", definition["name"], definition.get("description", ""), definition.get("input_schema", {}), allowed_modes=("assistant", "coding"), side_effect=definition["name"] in {"write_file", "edit_file", "bash"}))
        if "mcp" in required:
            for item in self.app.mcp.list_capabilities("assistant"):
                results.append(Capability(f"mcp:{item['server_id']}:{item['name']}", "mcp", item["name"], item["description"], item["input_schema"], item.get("risk_level", "read_only")))
        return results
