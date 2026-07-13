import json
from dataclasses import asdict, dataclass


@dataclass
class ToolResult:
    ok: bool
    content: str = ""
    error_type: str = ""
    message: str = ""
    recoverable: bool = True

    @classmethod
    def success(cls, content: str) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def error(
        cls,
        error_type: str,
        message: str,
        recoverable: bool = True,
    ) -> "ToolResult":
        return cls(
            ok=False,
            error_type=error_type,
            message=message,
            recoverable=recoverable,
        )

    def to_tool_content(self) -> str:
        if self.ok:
            return self.content
        return json.dumps(asdict(self), ensure_ascii=False)


class ToolCallValidator:
    def validate_block(self, block) -> str | None:
        if getattr(block, "type", None) != "tool_use":
            return "Expected a tool_use block."
        if not getattr(block, "id", None):
            return "tool_use block is missing id."
        if not isinstance(getattr(block, "name", None), str) or not block.name:
            return "tool_use block is missing tool name."
        if not isinstance(getattr(block, "input", None), dict):
            return "tool_use input must be an object."
        return None

    def validate_input(self, definition: dict, tool_input: dict) -> str | None:
        schema = definition.get("input_schema", {})
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for key in required:
            if key not in tool_input:
                return f"Missing required argument: {key}"

        for key, value in tool_input.items():
            expected = properties.get(key)
            if expected is None:
                return f"Unknown argument: {key}"

            error = self.validate_value(key, value, expected)
            if error:
                return error

        return None

    def validate_value(self, key: str, value, schema: dict) -> str | None:
        expected_type = schema.get("type")

        if expected_type == "string" and not isinstance(value, str):
            return f"Argument {key} must be a string."
        if expected_type == "integer" and not self.is_integer(value):
            return f"Argument {key} must be an integer."
        if expected_type == "number" and not self.is_number(value):
            return f"Argument {key} must be a number."
        if expected_type == "array" and not isinstance(value, list):
            return f"Argument {key} must be an array."
        if expected_type == "object" and not isinstance(value, dict):
            return f"Argument {key} must be an object."
        if expected_type == "boolean" and not isinstance(value, bool):
            return f"Argument {key} must be a boolean."

        allowed = schema.get("enum")
        if allowed and value not in allowed:
            return f"Argument {key} must be one of: {', '.join(allowed)}"

        if expected_type == "array":
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    error = self.validate_value(f"{key}[{index}]", item, item_schema)
                    if error:
                        return error

        if expected_type == "object":
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for child_key in required:
                if child_key not in value:
                    return f"Argument {key}.{child_key} is required."
            for child_key, child_value in value.items():
                child_schema = properties.get(child_key)
                if child_schema is None:
                    additional = schema.get("additionalProperties", False)
                    if additional is True:
                        continue
                    if isinstance(additional, dict):
                        child_schema = additional
                    else:
                        return f"Unknown argument: {key}.{child_key}"
                error = self.validate_value(f"{key}.{child_key}", child_value, child_schema)
                if error:
                    return error

        return None

    def is_integer(self, value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def is_number(self, value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
