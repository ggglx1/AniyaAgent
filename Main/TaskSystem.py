import json
import random
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None


class TaskSystem:
    valid_statuses = {"pending", "in_progress", "completed"}

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.tasks_dir = self.workdir / ".tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def create_task(
        self,
        subject: str,
        description: str = "",
        blockedBy: list[str] | None = None,
    ) -> str:
        task = Task(
            id=self.new_task_id(),
            subject=subject,
            description=description,
            status="pending",
            owner=None,
            blockedBy=blockedBy or [],
            worktree=None,
        )
        self.save_task(task)
        deps = f" (blockedBy: {', '.join(task.blockedBy)})" if task.blockedBy else ""
        return f"Created {task.id}: {task.subject}{deps}"

    def list_tasks(self) -> str:
        tasks = self.load_all_tasks()
        if not tasks:
            return "No tasks. Use create_task to add tasks."

        lines = []
        for task in tasks:
            deps = f" blockedBy={task.blockedBy}" if task.blockedBy else ""
            owner = f" owner={task.owner}" if task.owner else ""
            worktree = f" worktree={task.worktree}" if task.worktree else ""
            lines.append(
                f"{task.id}: {task.subject} [{task.status}]{owner}{deps}{worktree}"
            )
        return "\n".join(lines)

    def get_task(self, task_id: str) -> str:
        task = self.load_task(task_id)
        return json.dumps(asdict(task), indent=2, ensure_ascii=False)

    def claim_task(self, task_id: str, owner: str = "agent") -> str:
        with self.lock:
            task = self.load_task(task_id)
            if task.status != "pending":
                return f"Task {task.id} is {task.status}, cannot claim"
            if task.owner:
                return f"Task {task.id} already owned by {task.owner}"

            blockers = self.blocking_dependencies(task)
            if blockers:
                return f"Blocked by: {blockers}"

            task.owner = owner
            task.status = "in_progress"
            self.save_task(task)
            return f"Claimed {task.id} ({task.subject})"

    def complete_task(self, task_id: str) -> str:
        task = self.load_task(task_id)
        if task.status != "in_progress":
            return f"Task {task.id} is {task.status}, cannot complete"

        task.status = "completed"
        self.save_task(task)

        unblocked = [
            item.subject
            for item in self.load_all_tasks()
            if item.status == "pending" and item.blockedBy and self.can_start(item.id)
        ]
        message = f"Completed {task.id} ({task.subject})"
        if unblocked:
            message += f"\nUnblocked: {', '.join(unblocked)}"
        return message

    def scan_unclaimed_tasks(self) -> list[Task]:
        return [
            task
            for task in self.load_all_tasks()
            if task.status == "pending" and not task.owner and self.can_start(task.id)
        ]

    def bind_worktree(self, task_id: str, worktree_name: str) -> str:
        task = self.load_task(task_id)
        task.worktree = worktree_name
        self.save_task(task)
        return f"Bound {task.id} ({task.subject}) to worktree {worktree_name}"

    def can_start(self, task_id: str) -> bool:
        return not self.blocking_dependencies(self.load_task(task_id))

    def blocking_dependencies(self, task: Task) -> list[str]:
        blockers = []
        for dep_id in task.blockedBy:
            if not self.task_path(dep_id).exists():
                blockers.append(dep_id)
                continue
            dep = self.load_task(dep_id)
            if dep.status != "completed":
                blockers.append(dep_id)
        return blockers

    def save_task(self, task: Task) -> None:
        if task.status not in self.valid_statuses:
            raise ValueError(f"Invalid task status: {task.status}")
        self.task_path(task.id).write_text(
            json.dumps(asdict(task), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_task(self, task_id: str) -> Task:
        path = self.task_path(task_id)
        if not path.exists():
            raise FileNotFoundError(f"Task not found: {task_id}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        data["blockedBy"] = data.get("blockedBy") or []
        data["worktree"] = data.get("worktree")
        return Task(**data)

    def load_all_tasks(self) -> list[Task]:
        tasks = []
        for path in sorted(self.tasks_dir.glob("task_*.json")):
            try:
                tasks.append(self.load_task(path.stem))
            except Exception:
                continue
        return tasks

    def task_path(self, task_id: str) -> Path:
        safe_id = self.safe_task_id(task_id)
        return self.tasks_dir / f"{safe_id}.json"

    def new_task_id(self) -> str:
        return f"task_{int(time.time())}_{random.randint(0, 9999):04d}"

    def safe_task_id(self, task_id: str) -> str:
        if not task_id.startswith("task_"):
            raise ValueError("Task id must start with task_")
        safe = "".join(ch for ch in task_id if ch.isalnum() or ch in "_-")
        if safe != task_id:
            raise ValueError(f"Invalid task id: {task_id}")
        return safe
