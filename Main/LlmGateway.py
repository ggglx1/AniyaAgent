import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LlmRequest:
    task_type: str
    kwargs: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: Any = None
    error: BaseException | None = None
    queued_at: float = field(default_factory=time.time)


class QueuedMessagesClient:
    def __init__(self, gateway: "LlmGateway"):
        self.gateway = gateway

    def create(self, *, task_type: str = "main", **kwargs):
        return self.gateway.create_message(task_type=task_type, **kwargs)


class LlmGateway:
    """Single LLM entrypoint with request queue and task-based model routing."""

    def __init__(
        self,
        base_client,
        primary_model: str,
        max_concurrent: int | None = None,
        logger: Callable[[str], None] | None = None,
    ):
        self.base_client = base_client
        self.primary_model = primary_model
        self.max_concurrent = max_concurrent or int(os.getenv("LLM_MAX_CONCURRENT", "1"))
        self.logger = logger or (lambda _: None)
        self.messages = QueuedMessagesClient(self)
        self.requests: queue.Queue[LlmRequest | None] = queue.Queue()
        self.workers = []

        for index in range(max(1, self.max_concurrent)):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"llm-gateway-{index + 1}",
                daemon=True,
            )
            worker.start()
            self.workers.append(worker)

    def create_message(self, *, task_type: str = "main", **kwargs):
        request = LlmRequest(task_type=task_type, kwargs=kwargs)
        self.requests.put(request)
        request.event.wait()

        if request.error is not None:
            raise request.error
        return request.response

    def _worker_loop(self) -> None:
        while True:
            request = self.requests.get()
            if request is None:
                self.requests.task_done()
                return

            try:
                kwargs = dict(request.kwargs)
                kwargs["model"] = self.resolve_model(request.task_type, kwargs.get("model"))
                waited = time.time() - request.queued_at
                if waited > 0.25:
                    self.logger(
                        f"[llm queue] {request.task_type} waited {waited:.2f}s "
                        f"(pending={self.requests.qsize()})"
                    )
                request.response = self.base_client.messages.create(**kwargs)
            except BaseException as exc:
                request.error = exc
            finally:
                request.event.set()
                self.requests.task_done()

    def resolve_model(self, task_type: str, requested_model: str | None) -> str:
        routed_model = self.model_for_task(task_type)
        if routed_model:
            return routed_model
        return requested_model or self.primary_model

    def model_for_task(self, task_type: str) -> str | None:
        env_name = {
            "main": "MODEL_ID",
            "team": "TEAM_MODEL_ID",
            "memory_match": "MEMORY_MODEL_ID",
            "memory_extract": "MEMORY_MODEL_ID",
            "memory_consolidate": "MEMORY_MODEL_ID",
            "compact": "COMPACT_MODEL_ID",
            "compact_chunk": "COMPACT_MODEL_ID",
            "compact_merge": "COMPACT_MODEL_ID",
            "structured_repair": "REPAIR_MODEL_ID",
        }.get(task_type)

        if not env_name:
            return None
        return os.getenv(env_name) or None
