import json
import re
from dataclasses import dataclass

from main.tools.tool_result import ToolCallValidator


class StructuredOutputError(ValueError):
    pass


class ModelOutputValidator:
    def validate_response_content(self, content) -> str | None:
        if not isinstance(content, list):
            return "Model response content must be a list."
        for index, block in enumerate(content):
            block_type = getattr(block, "type", None)
            if block_type is None and isinstance(block, dict):
                block_type = block.get("type")
            if block_type not in {"text", "tool_use"}:
                return f"Unsupported model response block type at index {index}: {block_type}"
            if block_type == "tool_use":
                if not getattr(block, "id", None):
                    return f"tool_use block at index {index} is missing id."
                if not isinstance(getattr(block, "name", None), str):
                    return f"tool_use block at index {index} is missing tool name."
                if not isinstance(getattr(block, "input", None), dict):
                    return f"tool_use block at index {index} input must be an object."
        return None


@dataclass
class StructuredOutputParser:
    max_repair_attempts: int = 2

    def __post_init__(self):
        self.validator = ToolCallValidator()

    def parse_json(self, text: str, schema: dict | None = None):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = self.parse_embedded_json(text)

        if schema:
            error = self.validator.validate_value("output", value, schema)
            if error:
                raise StructuredOutputError(error)

        return value

    def parse_embedded_json(self, text: str):
        match = self.find_json_array(text) or self.find_json_object(text)
        if not match:
            raise StructuredOutputError("No JSON object or array found in model output.")

        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(f"Invalid JSON: {exc}") from exc

    def find_json_array(self, text: str):
        return re.search(r"\[.*\]", text, re.DOTALL)

    def find_json_object(self, text: str):
        return re.search(r"\{.*\}", text, re.DOTALL)

    def repair_json(
        self,
        client,
        *,
        model: str,
        text: str,
        schema: dict,
        instruction: str,
        max_tokens: int = 1200,
    ):
        last_error = None
        current_text = text

        for _ in range(self.max_repair_attempts):
            try:
                return self.parse_json(current_text, schema)
            except StructuredOutputError as exc:
                last_error = exc
                prompt = (
                    "The previous model output did not match the required JSON format.\n"
                    f"Parse error: {exc}\n\n"
                    f"Required format:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                    f"Task instruction:\n{instruction}\n\n"
                    f"Previous output:\n{current_text}\n\n"
                    "Return only valid JSON. Do not include markdown fences or explanation."
                )
                response = client.messages.create(
                    task_type="structured_repair",
                    model=model,
                    system="You repair invalid structured JSON output.",
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                    max_tokens=max_tokens,
                )
                current_text = self.extract_text(response.content).strip()

        try:
            return self.parse_json(current_text, schema)
        except StructuredOutputError as exc:
            raise StructuredOutputError(
                f"Structured output repair failed: {exc}"
            ) from last_error

    def extract_text(self, content) -> str:
        if not isinstance(content, list):
            return str(content)

        parts = []
        for block in content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
