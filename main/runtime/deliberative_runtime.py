from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from main.agent.conversation_integrity import ConversationIntegrityValidator
from main.llm.usage import bind_request_context
from main.runtime.models import RunStatus, UnifiedRunResult
from main.tools.tool_result import ToolResult


class McpToolProxy:
    def __init__(self, gateway, capability, token):
        self.gateway = gateway; self.capability = capability; self.token = token
        self.name = f"mcp_{self._safe(capability.server_id if hasattr(capability, 'server_id') else capability.id)}_{self._safe(capability.name)}"

    def _safe(self, value):
        return "".join(char if char.isalnum() or char == "_" else "_" for char in str(value))[:64]

    @property
    def definition(self):
        return {"name": self.name, "description": self.capability.description, "input_schema": self.capability.input_schema}

    def run(self, **arguments):
        _, server_id, capability_name = self.capability.id.split(":", 2)
        result = self.gateway.invoke(server_id, capability_name, arguments, {"token": self.token, "approved": self.capability.risk_level == "read_only"})
        return str(result.get("data", result))


class CapabilityToolSlice:
    """A per-run view of local tools; the global registry is never exposed directly."""

    def __init__(self, tools, capabilities, *, allow_side_effects: bool = False, mcp_gateway=None, token=None):
        allowed = {
            item.name for item in capabilities
            if item.provider == "local" and (allow_side_effects or not item.side_effect)
        }
        self.registry = {name: tool for name, tool in getattr(tools, "registry", {}).items() if name in allowed}
        for capability in capabilities:
            if capability.provider == "mcp" and (allow_side_effects or not capability.side_effect):
                self.registry[capability.id] = McpToolProxy(mcp_gateway, capability, token)
        self.validator = getattr(tools, "validator", None)

    @property
    def definitions(self) -> list[dict]:
        return [tool.definition for tool in self.registry.values()]

    def execute(self, block, before_execute=None) -> str:
        tool = self.registry.get(getattr(block, "name", ""))
        if tool is None:
            return ToolResult.error("CapabilityDenied", "This capability is not available in the current run.", recoverable=True).to_tool_content()
        if self.validator is not None:
            error = self.validator.validate_block(block) or self.validator.validate_input(tool.definition, getattr(block, "input", {}) or {})
            if error:
                return ToolResult.error("InvalidToolInput", error, recoverable=True).to_tool_content()
        try:
            denied = before_execute(block) if before_execute else None
            if denied:
                return ToolResult.error("PermissionDenied", str(denied), recoverable=False).to_tool_content()
            output = tool.run(**(getattr(block, "input", {}) or {}))
        except Exception as exc:
            return ToolResult.error(type(exc).__name__, str(exc), recoverable=True).to_tool_content()
        if isinstance(output, str) and output.startswith("Error:"):
            return ToolResult.error("ToolExecutionError", output, recoverable=True).to_tool_content()
        return ToolResult.success(str(output)).to_tool_content()


class ToolResultCompactor:
    """Preserve full tool output as an artifact while bounding model-visible tokens."""

    def __init__(self, workdir: Path, per_tool_tokens: int = 1_200, run_tokens: int = 6_000):
        self.root = workdir / ".runtime" / "tool_artifacts"; self.root.mkdir(parents=True, exist_ok=True)
        self.per_tool_tokens = per_tool_tokens; self.run_tokens = run_tokens; self.used_tokens = 0

    def compact(self, value: str, tool_id: str) -> str:
        text = str(value); tokens = max(1, len(text) // 4); allowed = min(self.per_tool_tokens, max(0, self.run_tokens - self.used_tokens))
        self.used_tokens += min(tokens, allowed)
        if tokens <= allowed:
            return text
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        artifact_id = f"tool_{tool_id or digest[:12]}"
        path = self.root / f"{artifact_id}.txt"; path.write_text(text, encoding="utf-8")
        preview = text[: max(200, allowed * 4)] if allowed else ""
        return json.dumps({"status": "truncated", "artifact_id": artifact_id, "path": str(path), "sha256": digest, "original_tokens": tokens, "visible_tokens": max(1, len(preview) // 4), "reason": "tool_result_budget", "preview": preview}, ensure_ascii=False)


class DeliberativeRuntimeAdapter:
    """Bounded ReAct engine owned by RunCoordinator, with a capability-scoped tool table."""

    def __init__(self, application):
        self.app = application

    def execute(self, request, context) -> UnifiedRunResult:
        runtime = self.app.runtime
        built = context["built_context"]
        capabilities = context["capabilities"]
        read_tools = CapabilityToolSlice(runtime.tools, capabilities, mcp_gateway=self.app.mcp, token=context["token"])
        all_tools = CapabilityToolSlice(runtime.tools, capabilities, allow_side_effects=True, mcp_gateway=self.app.mcp, token=context["token"])
        system = (
            "You are Aniya's bounded task agent. Work only with the supplied capabilities. "
            "Start with read-only inspection. Explain before any destructive change, and finish with a concise result."
        )
        user_content = f"{built.text}\n\n{request.text}" if built.text else request.text
        images = self.app.attachments.model_image_blocks(list(request.metadata.get("attachment_ids") or []), owner_id=request.user_id)
        content = [{"type": "text", "text": user_content}, *images] if images else user_content
        messages = [{"role": "user", "content": content}]
        used = []
        escalated = False
        compactor = ToolResultCompactor(runtime.WORKDIR)
        integrity = ConversationIntegrityValidator()
        max_turns = 8
        run_context = {"run_id": request.run_id, "executor": "deliberative_agent", "route": "deliberative_agent", "context_blocks": built.metadata().get("blocks", [])}
        with bind_request_context(run_context):
            for turn in range(max_turns):
                context["token"].check()
                report = integrity.validate(messages)
                if not report.valid:
                    repaired, repaired_report = integrity.repair(messages)
                    if not repaired_report.valid:
                        return UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error="Invalid tool transaction history.", error_code="tool_transaction_integrity")
                    messages[:] = repaired
                toolset = all_tools if escalated else read_tools
                definitions = list(toolset.definitions)
                if not escalated:
                    definitions.append({"name": "request_capability_upgrade", "description": "Request one action-phase capability category after read-only inspection.", "input_schema": {"type": "object", "properties": {"category": {"type": "string", "enum": ["filesystem_write", "shell_readonly", "mcp_write"]}, "reason": {"type": "string"}, "target": {"type": "string"}}, "required": ["category", "reason", "target"]}})
                response = runtime.llm_gateway.messages.create(
                    task_type="main", model=runtime.MODEL, max_tokens=1800, system=system,
                    messages=messages, tools=definitions, cancellation_token=context["token"], run_context=run_context,
                )
                context["token"].check()
                blocks = list(getattr(response, "content", []) or [])
                messages.append({"role": "assistant", "content": blocks})
                if getattr(response, "stop_reason", "") != "tool_use":
                    output = runtime.extract_text(blocks).strip()
                    if output:
                        return UnifiedRunResult(request.run_id, RunStatus.COMPLETED.value, output, metadata={"executor": "deliberative", "memory_sources": built.source_ids, "tool_calls": used, "capability_escalated": escalated})
                    return UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error="Model returned an empty response.", error_code="empty_model_output", metadata={"executor": "deliberative"})
                results = []
                for block in blocks:
                    if getattr(block, "type", "") != "tool_use":
                        continue
                    name = getattr(block, "name", "")
                    if name == "request_capability_upgrade" and not escalated:
                        upgrade = getattr(block, "input", {}) or {}
                        category = str(upgrade.get("category") or "")
                        if category not in {"filesystem_write", "shell_readonly", "mcp_write"}:
                            results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Invalid capability category."})
                            continue
                        escalated = True
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"Capability upgrade granted for {category}. Use only the newly exposed minimum tools."})
                        continue
                    context["emit"]("tool.call.started", {"tool": {"id": block.id, "name": name, "input": getattr(block, "input", {})}})
                    def permission(block_to_check):
                        tool = toolset.registry.get(getattr(block_to_check, "name", ""))
                        if tool is not None and tool in all_tools.registry.values() and tool not in read_tools.registry.values():
                            return None if runtime.permissions.ask_user(block_to_check, "Deliberative action-phase tool") else "User confirmation is required for this side-effect tool."
                        return runtime.hooks.trigger("PreToolUse", block_to_check)
                    output = toolset.execute(block, permission)
                    output = compactor.compact(output, str(getattr(block, "id", "")))
                    used.append(name)
                    context["emit"]("tool.call.completed", {"tool": {"id": block.id, "name": name}, "result": {"preview": str(output)[:1000], "truncated": len(str(output)) > 1000}})
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                messages.append({"role": "user", "content": results})
        return UnifiedRunResult(request.run_id, RunStatus.FAILED.value, error="Deliberative turn budget reached before a final answer.", error_code="react_turn_budget", metadata={"executor": "deliberative", "tool_calls": used})
