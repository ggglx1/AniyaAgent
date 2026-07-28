from __future__ import annotations

from .models import Capability


class CapabilityCatalog:
    """Describes capabilities without forcing every tool into every model prompt."""
    def __init__(self, application): self.app = application

    def select(self, required: list[str], mode: str) -> list[Capability]:
        if mode in {"direct_conversation", "qa", "structured_action", "proactive"}: return []
        results: list[Capability] = []
        categories = set(required)
        if "local_tools" in categories:
            categories.update({"filesystem_read", "filesystem_write", "shell_readonly", "personal_state_read", "personal_state_write"})
        if categories & {"filesystem_read", "filesystem_write", "shell_readonly", "personal_state_read", "personal_state_write"}:
            tools = getattr(self.app.runtime, "tools", None)
            for definition in list(getattr(tools, "definitions", []) or []):
                name = definition["name"]
                category = (
                    "filesystem_read" if name in {"read_file", "glob"} else
                    "filesystem_write" if name in {"write_file", "edit_file"} else
                    "shell_readonly" if name == "bash" else
                    "personal_state_write" if name.startswith(("create_", "update_", "delete_")) else "personal_state_read"
                )
                if category in categories:
                    results.append(Capability(f"local:{name}", "local", name, definition.get("description", ""), definition.get("input_schema", {}), allowed_modes=("assistant", "coding"), side_effect=category in {"filesystem_write", "shell_readonly", "personal_state_write"}))
        if categories & {"mcp", "mcp_read", "mcp_write"}:
            for item in self.app.mcp.list_capabilities("assistant"):
                risk = item.get("risk_level", "read_only")
                category = "mcp_read" if risk == "read_only" else "mcp_write"
                if category in categories or "mcp" in categories:
                    results.append(Capability(f"mcp:{item['server_id']}:{item['name']}", "mcp", item["name"], item["description"], item["input_schema"], risk, side_effect=risk != "read_only"))
        return results
