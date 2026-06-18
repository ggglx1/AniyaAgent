import json
import threading
import time


class BackgroundTasks:
    slow_keywords = [
        "install",
        "build",
        "deploy",
        "compile",
        "docker build",
        "pip install",
        "npm install",
        "cargo build",
        "pytest",
        "make",
    ]

    def __init__(self):
        self.counter = 0
        self.tasks = {}
        self.results = {}
        self.lock = threading.Lock()

    def should_run_background(self, block) -> bool:
        if getattr(block, "name", "") != "bash":
            return False
        tool_input = getattr(block, "input", {}) or {}
        if tool_input.get("run_in_background"):
            return True
        command = tool_input.get("command", "").lower()
        return any(keyword in command for keyword in self.slow_keywords)

    def start(self, block, executor) -> str:
        bg_id = self.next_id()
        command = (getattr(block, "input", {}) or {}).get("command", "")

        with self.lock:
            self.tasks[bg_id] = {
                "tool_use_id": block.id,
                "command": command,
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
                "cancel_requested": False,
            }

        def worker():
            try:
                output = executor(block)
                status = "completed"
            except Exception as exc:
                output = f"Error: {type(exc).__name__}: {exc}"
                status = "failed"
            with self.lock:
                if self.tasks.get(bg_id, {}).get("cancel_requested"):
                    status = "cancelled"
                self.tasks[bg_id]["status"] = status
                self.tasks[bg_id]["finished_at"] = time.time()
                self.results[bg_id] = output

        threading.Thread(target=worker, daemon=True).start()
        return bg_id

    def collect_notifications(self) -> list[str]:
        with self.lock:
            ready_ids = [
                bg_id
                for bg_id, task in self.tasks.items()
                if task["status"] in {"completed", "failed", "cancelled"}
            ]

        notifications = []
        for bg_id in ready_ids:
            with self.lock:
                task = self.tasks.pop(bg_id)
                output = self.results.pop(bg_id, "")

            summary = str(output)[:500]
            notifications.append(
                "<task_notification>\n"
                f"  <task_id>{bg_id}</task_id>\n"
                f"  <status>{task['status']}</status>\n"
                f"  <command>{task['command']}</command>\n"
                f"  <summary>{summary}</summary>\n"
                "</task_notification>"
            )
        return notifications

    def list_tasks(self) -> str:
        with self.lock:
            snapshot = {
                bg_id: self.public_task(bg_id, task)
                for bg_id, task in sorted(self.tasks.items())
            }
        if not snapshot:
            return "No background tasks."
        return json.dumps(snapshot, indent=2, ensure_ascii=False)

    def get_task(self, task_id: str) -> str:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return f"Error: Background task {task_id} not found"
            payload = self.public_task(task_id, task)
            if task["status"] in {"completed", "failed", "cancelled"}:
                payload["result"] = self.results.get(task_id, "")
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def cancel_task(self, task_id: str) -> str:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return f"Error: Background task {task_id} not found"
            if task["status"] in {"completed", "failed", "cancelled"}:
                return f"Background task {task_id} is already {task['status']}"
            task["cancel_requested"] = True
        return (
            f"Cancellation requested for {task_id}. "
            "Current implementation cannot force-kill a running thread; "
            "the task will be marked cancelled when the underlying tool returns."
        )

    def wait_task(self, task_id: str, timeout_seconds: int = 30) -> str:
        deadline = time.time() + max(0, timeout_seconds)
        while True:
            with self.lock:
                task = self.tasks.get(task_id)
                if task is None:
                    return f"Error: Background task {task_id} not found"
                if task["status"] in {"completed", "failed", "cancelled"}:
                    payload = self.public_task(task_id, task)
                    payload["result"] = self.results.get(task_id, "")
                    return json.dumps(payload, indent=2, ensure_ascii=False)

            if time.time() >= deadline:
                return f"Background task {task_id} is still running after {timeout_seconds}s"
            time.sleep(0.2)

    def next_id(self) -> str:
        with self.lock:
            self.counter += 1
            return f"bg_{self.counter:04d}"

    def public_task(self, task_id: str, task: dict) -> dict:
        started_at = task.get("started_at")
        finished_at = task.get("finished_at")
        now = time.time()
        elapsed = (finished_at or now) - started_at if started_at else 0
        return {
            "id": task_id,
            "tool_use_id": task.get("tool_use_id"),
            "command": task.get("command"),
            "status": task.get("status"),
            "elapsed_seconds": round(elapsed, 2),
            "cancel_requested": bool(task.get("cancel_requested")),
        }
