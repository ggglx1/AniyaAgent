from __future__ import annotations


class McpPolicy:
    risky = {"write_reversible", "write_irreversible", "sensitive"}
    def allowed(self, config, capability, mode: str) -> bool: return bool(config.enabled and mode in config.allowed_modes)
    def requires_confirmation(self, capability) -> bool: return capability.risk_level in self.risky
