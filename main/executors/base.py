from __future__ import annotations

from typing import Protocol


class Executor(Protocol):
    def execute(self, request, context, decision): ...
