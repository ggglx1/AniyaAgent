import json
import re
import subprocess
import time
from pathlib import Path

from main.tasks.task_system import TaskSystem


class WorktreeManager:
    valid_name = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

    def __init__(self, workdir: Path, task_system: TaskSystem):
        self.workdir = workdir.resolve()
        self.task_system = task_system
        self.worktrees_dir = self.workdir / ".worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.worktrees_dir / "events.jsonl"

    def create_worktree(self, name: str, task_id: str = "") -> str:
        error = self.validate_name(name)
        if error:
            return f"Error: {error}"

        path = self.worktree_path(name)
        if path.exists():
            return f"Error: Worktree {name} already exists at {path}"

        ok, output = self.run_git(
            ["worktree", "add", str(path), "-b", self.branch_name(name), "HEAD"]
        )
        if not ok:
            return f"Error: git worktree add failed: {output}"

        bind_message = ""
        if task_id:
            try:
                bind_message = "\n" + self.bind_task_to_worktree(task_id, name)
            except Exception as exc:
                bind_message = f"\nWarning: worktree created but task binding failed: {exc}"

        self.log_event("create", name, task_id)
        return f"Worktree {name} created at {path}{bind_message}"

    def bind_task_to_worktree(self, task_id: str, worktree_name: str) -> str:
        error = self.validate_name(worktree_name)
        if error:
            return f"Error: {error}"

        path = self.worktree_path(worktree_name)
        if not path.exists():
            return f"Error: Worktree {worktree_name} does not exist at {path}"

        message = self.task_system.bind_worktree(task_id, worktree_name)
        self.log_event("bind", worktree_name, task_id)
        return message

    def remove_worktree(self, name: str, discard_changes: bool = False) -> str:
        error = self.validate_name(name)
        if error:
            return f"Error: {error}"

        path = self.worktree_path(name)
        if not path.exists():
            return f"Error: Worktree {name} not found"

        if not discard_changes:
            files_changed = self.count_changed_files(path)
            if files_changed is None:
                return (
                    f"Error: Cannot verify status for {name}. "
                    "Use discard_changes=true only if you intentionally want to force removal."
                )
            if files_changed > 0:
                return (
                    f"Error: Worktree {name} has {files_changed} changed file(s). "
                    "Use keep_worktree for review, or discard_changes=true to force removal."
                )

        args = ["worktree", "remove", str(path)]
        if discard_changes:
            args.append("--force")
        ok, output = self.run_git(args)
        if not ok:
            return f"Error: git worktree remove failed: {output}"

        branch = self.branch_name(name)
        if discard_changes:
            self.run_git(["branch", "-D", branch])
            branch_message = f" Branch {branch} was force-deleted."
        else:
            ok, branch_output = self.run_git(["branch", "-d", branch])
            branch_message = (
                f" Branch {branch} was deleted."
                if ok
                else f" Branch {branch} was kept: {branch_output}"
            )

        self.log_event("remove", name)
        return f"Worktree {name} removed.{branch_message}"

    def keep_worktree(self, name: str) -> str:
        error = self.validate_name(name)
        if error:
            return f"Error: {error}"

        path = self.worktree_path(name)
        if not path.exists():
            return f"Error: Worktree {name} not found"

        self.log_event("keep", name)
        return f"Worktree {name} kept for review at {path} on branch {self.branch_name(name)}"

    def validate_name(self, name: str) -> str | None:
        if not name:
            return "Worktree name cannot be empty"
        if name in {".", ".."}:
            return f"Invalid worktree name: {name}"
        if not self.valid_name.fullmatch(name):
            return (
                "Worktree name must be 1-64 chars and only contain "
                "letters, digits, dot, underscore, or dash"
            )
        return None

    def worktree_path(self, name: str) -> Path:
        path = (self.worktrees_dir / name).resolve()
        try:
            path.relative_to(self.worktrees_dir.resolve())
        except ValueError:
            raise ValueError(f"Worktree path escapes .worktrees: {name}")
        return path

    def branch_name(self, name: str) -> str:
        return f"wt/{name}"

    def count_changed_files(self, path: Path) -> int | None:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if result.returncode != 0:
            return None
        return len([line for line in result.stdout.splitlines() if line.strip()])

    def run_git(self, args: list[str]) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return False, "git command timed out"
        except OSError as exc:
            return False, str(exc)

        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output[:5000] if output else "(no output)"

    def log_event(self, event_type: str, worktree_name: str, task_id: str = "") -> None:
        event = {
            "type": event_type,
            "worktree": worktree_name,
            "task_id": task_id,
            "ts": time.time(),
        }
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
