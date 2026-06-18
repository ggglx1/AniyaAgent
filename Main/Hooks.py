class Hooks:
    def __init__(self):
        self.registry = {
            "UserPromptSubmit": [],
            "PreToolUse": [],
            "PostToolUse": [],
            "Stop": [],
        }

    def register(self, event: str, callback):
        if event not in self.registry:
            raise ValueError(f"Unknown hook event: {event}")
        self.registry[event].append(callback)

    def trigger(self, event: str, *args):
        if event not in self.registry:
            raise ValueError(f"Unknown hook event: {event}")

        for callback in self.registry[event]:
            result = callback(*args)
            if result is not None:
                return result

        return None
