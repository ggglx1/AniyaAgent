class Block(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class MessageResponse:
    def __init__(self, content: list[dict], stop_reason: str, raw: dict):
        self.raw = raw
        self.content = [Block(block) for block in content]
        self.stop_reason = stop_reason
