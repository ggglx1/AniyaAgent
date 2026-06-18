import json
import random
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)


class MessageBus:
    def __init__(self, workdir: Path):
        self.mailbox_dir = workdir.resolve() / ".mailboxes"
        self.mailbox_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "message",
        metadata: dict | None = None,
    ) -> None:
        message = {
            "from": from_agent,
            "to": to_agent,
            "type": msg_type,
            "content": content,
            "ts": time.time(),
            "metadata": metadata or {},
        }
        path = self.mailbox_path(to_agent)
        with self.lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    def read_inbox(self, agent: str) -> list[dict]:
        path = self.mailbox_path(agent)
        if not path.exists():
            return []

        with self.lock:
            if not path.exists():
                return []
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            path.unlink()

        messages = []
        for line in lines:
            try:
                item = json.loads(line)
                item.setdefault("metadata", {})
                messages.append(item)
            except json.JSONDecodeError:
                continue
        return messages

    def format_inbox(self, messages: list[dict]) -> str:
        if not messages:
            return "No messages."
        lines = []
        for item in messages:
            metadata = item.get("metadata", {})
            request_id = metadata.get("request_id")
            suffix = f" req:{request_id}" if request_id else ""
            lines.append(
                f"From {item.get('from')} [{item.get('type')}{suffix}]: "
                f"{item.get('content')}"
            )
        return "\n".join(lines)

    def mailbox_path(self, agent: str) -> Path:
        return self.mailbox_dir / f"{self.safe_agent_name(agent)}.jsonl"

    def safe_agent_name(self, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip())
        return safe.strip("-") or "agent"


class AgentTeams:
    idle_poll_interval = 5
    idle_timeout = 60
    work_turn_limit = 10

    def __init__(
        self,
        workdir: Path,
        client,
        model: str,
        tool_factory,
        task_system=None,
        worktree_manager=None,
    ):
        self.workdir = workdir.resolve()
        self.client = client
        self.model = model
        self.tool_factory = tool_factory
        self.task_system = task_system
        self.worktree_manager = worktree_manager
        self.bus = MessageBus(self.workdir)
        self.active_teammates = {}
        self.pending_requests: dict[str, ProtocolState] = {}
        self.lock = threading.Lock()

    def spawn_teammate(self, name: str, role: str, prompt: str) -> str:
        safe_name = self.bus.safe_agent_name(name)
        with self.lock:
            if safe_name in self.active_teammates:
                return f"Teammate {safe_name} is already running."
            self.active_teammates[safe_name] = {
                "role": role,
                "status": "running",
                "started_at": time.time(),
                "workdir": str(self.workdir),
            }

        thread = threading.Thread(
            target=self.run_teammate,
            args=(safe_name, role, prompt),
            daemon=True,
        )
        thread.start()
        return f"Spawned autonomous teammate {safe_name} as {role}."

    def send_message(
        self,
        to_agent: str,
        content: str,
        from_agent: str = "lead",
        msg_type: str = "message",
        metadata: dict | None = None,
    ) -> str:
        self.bus.send(from_agent, to_agent, content, msg_type, metadata)
        return f"Sent {msg_type} to {to_agent}."

    def request_shutdown(self, teammate: str, reason: str = "") -> str:
        request_id = self.new_request_id()
        safe_name = self.bus.safe_agent_name(teammate)
        self.pending_requests[request_id] = ProtocolState(
            request_id=request_id,
            type="shutdown",
            sender="lead",
            target=safe_name,
            status="pending",
            payload=reason,
        )
        self.bus.send(
            "lead",
            safe_name,
            reason or "Please shut down gracefully.",
            "shutdown_request",
            {"request_id": request_id},
        )
        return f"Shutdown request sent to {safe_name} (request_id={request_id})."

    def request_plan(self, teammate: str, task: str) -> str:
        safe_name = self.bus.safe_agent_name(teammate)
        self.bus.send(
            "lead",
            safe_name,
            f"Please submit a plan before acting:\n{task}",
            "plan_request",
        )
        return f"Asked {safe_name} to submit a plan."

    def submit_plan(self, from_agent: str, plan: str) -> str:
        request_id = self.new_request_id()
        safe_name = self.bus.safe_agent_name(from_agent)
        self.pending_requests[request_id] = ProtocolState(
            request_id=request_id,
            type="plan_approval",
            sender=safe_name,
            target="lead",
            status="pending",
            payload=plan,
        )
        self.bus.send(
            safe_name,
            "lead",
            plan,
            "plan_approval_request",
            {"request_id": request_id},
        )
        return f"Plan submitted to lead (request_id={request_id}). Wait for approval."

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        state = self.pending_requests.get(request_id)
        if state is None:
            return f"Request {request_id} not found."
        if state.type != "plan_approval":
            return f"Request {request_id} is {state.type}, not plan_approval."
        if state.status != "pending":
            return f"Request {request_id} already {state.status}."

        state.status = "approved" if approve else "rejected"
        self.bus.send(
            "lead",
            state.sender,
            feedback or ("Approved." if approve else "Rejected."),
            "plan_approval_response",
            {"request_id": request_id, "approve": approve},
        )
        return f"Plan {state.status} for {state.sender} (request_id={request_id})."

    def check_inbox(self, agent: str = "lead") -> str:
        safe_agent = self.bus.safe_agent_name(agent)
        if safe_agent == "lead":
            messages = self.consume_lead_inbox(route_protocol=True)
        else:
            messages = self.bus.read_inbox(safe_agent)
        return self.bus.format_inbox(messages)

    def collect_lead_messages(self) -> list[str]:
        messages = self.consume_lead_inbox(route_protocol=True)
        if not messages:
            return []
        return [
            "<teammate-message>\n"
            f"  <from>{item.get('from')}</from>\n"
            f"  <type>{item.get('type')}</type>\n"
            f"  <metadata>{json.dumps(item.get('metadata', {}), ensure_ascii=False)}</metadata>\n"
            f"  <content>{item.get('content')}</content>\n"
            "</teammate-message>"
            for item in messages
        ]

    def consume_lead_inbox(self, route_protocol: bool = True) -> list[dict]:
        messages = self.bus.read_inbox("lead")
        if not route_protocol:
            return messages

        for message in messages:
            msg_type = message.get("type", "")
            metadata = message.get("metadata", {})
            request_id = metadata.get("request_id", "")
            if request_id and msg_type.endswith("_response"):
                self.match_response(msg_type, request_id, metadata.get("approve", False))
        return messages

    def match_response(self, response_type: str, request_id: str, approve: bool) -> None:
        state = self.pending_requests.get(request_id)
        if state is None:
            return
        if state.status != "pending":
            return
        if state.type == "shutdown" and response_type != "shutdown_response":
            return
        if state.type == "plan_approval" and response_type != "plan_approval_response":
            return

        state.status = "approved" if approve else "rejected"

    def protocol_status(self) -> str:
        if not self.pending_requests:
            return "No protocol requests."
        return json.dumps(
            {key: asdict(value) for key, value in self.pending_requests.items()},
            indent=2,
            ensure_ascii=False,
        )

    def new_request_id(self) -> str:
        while True:
            request_id = f"req_{random.randint(0, 999999):06d}"
            if request_id not in self.pending_requests:
                return request_id

    def run_teammate(self, name: str, role: str, prompt: str) -> None:
        messages = [{"role": "user", "content": prompt}]
        system = self.teammate_system_prompt(name, role)

        try:
            while True:
                self.reinject_identity_if_needed(name, role, messages)
                tools = self.tool_factory(name)
                should_shutdown = self.run_work_phase(name, tools, system, messages)
                if should_shutdown:
                    break

                idle_result = self.idle_poll(name, messages)
                if idle_result == "work":
                    continue
                break

            summary = self.extract_latest_text(messages) or "Teammate finished."
            self.bus.send(name, "lead", summary, "result")
        except Exception as exc:
            self.bus.send(
                name,
                "lead",
                f"Teammate error: {type(exc).__name__}: {exc}",
                "error",
            )
        finally:
            with self.lock:
                if name in self.active_teammates:
                    self.active_teammates[name]["status"] = "stopped"

    def run_work_phase(self, name: str, tools, system: str, messages: list) -> bool:
        for _ in range(self.work_turn_limit):
            inbox_result = self.dispatch_inbox(name, messages)
            if inbox_result == "shutdown":
                return True

            response = self.client.messages.create(
                model=self.model,
                system=system,
                messages=messages[-20:],
                tools=tools.definitions,
                max_tokens=8000,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return False

            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                output = tools.execute(block)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
            messages.append({"role": "user", "content": results})
        return False

    def idle_poll(self, name: str, messages: list) -> str:
        steps = max(1, self.idle_timeout // self.idle_poll_interval)
        for _ in range(steps):
            time.sleep(self.idle_poll_interval)

            inbox_result = self.dispatch_inbox(name, messages)
            if inbox_result == "shutdown":
                return "shutdown"
            if inbox_result == "work":
                return "work"

            claimed = self.claim_next_available_task(name)
            if claimed:
                task, result, worktree_note = claimed
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "<auto-claimed-task>\n"
                            f"id: {task.id}\n"
                            f"subject: {task.subject}\n"
                            f"description: {task.description}\n"
                            f"result: {result}\n"
                            f"{worktree_note}\n"
                            "</auto-claimed-task>"
                        ),
                    }
                )
                return "work"
        return "timeout"

    def dispatch_inbox(self, name: str, messages: list) -> str:
        inbox = self.bus.read_inbox(name)
        if not inbox:
            return "empty"

        normal_messages = []
        for message in inbox:
            action = self.dispatch_message(name, message, messages)
            if action == "shutdown":
                return "shutdown"
            if action == "normal":
                normal_messages.append(message)

        if normal_messages:
            messages.append(
                {
                    "role": "user",
                    "content": f"<inbox>\n{self.bus.format_inbox(normal_messages)}\n</inbox>",
                }
            )
            return "work"
        return "empty"

    def dispatch_message(self, name: str, message: dict, messages: list) -> str:
        msg_type = message.get("type", "message")
        metadata = message.get("metadata", {})
        request_id = metadata.get("request_id", "")

        if msg_type == "shutdown_request":
            self.bus.send(
                name,
                "lead",
                "Shutting down gracefully.",
                "shutdown_response",
                {"request_id": request_id, "approve": True},
            )
            return "shutdown"

        if msg_type == "plan_approval_response":
            approve = metadata.get("approve", False)
            status = "approved" if approve else "rejected"
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[Plan {status}] request_id={request_id}\n"
                        f"Feedback: {message.get('content', '')}"
                    ),
                }
            )
            return "handled"

        if msg_type == "plan_request":
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[Plan requested by lead]\n"
                        f"{message.get('content', '')}\n"
                        "Use submit_plan before high-risk changes."
                    ),
                }
            )
            return "handled"

        return "normal"

    def claim_next_available_task(self, name: str):
        if self.task_system is None:
            return None

        for task in self.task_system.scan_unclaimed_tasks():
            result = self.task_system.claim_task(task.id, owner=name)
            if not result.startswith("Claimed"):
                continue
            worktree_note = self.activate_task_worktree(name, task.id)
            return task, result, worktree_note
        return None

    def teammate_workdir(self, name: str) -> Path:
        safe_name = self.bus.safe_agent_name(name)
        with self.lock:
            state = self.active_teammates.get(safe_name, {})
            return Path(state.get("workdir") or self.workdir).resolve()

    def activate_task_worktree(self, agent_name: str, task_id: str) -> str:
        if self.task_system is None:
            return ""

        task = self.task_system.load_task(task_id)
        if not task.worktree:
            self.set_teammate_workdir(agent_name, self.workdir)
            return "Working directory: main workspace."

        if self.worktree_manager is not None:
            path = self.worktree_manager.worktree_path(task.worktree)
        else:
            path = (self.workdir / ".worktrees" / task.worktree).resolve()

        if not path.exists():
            return f"Warning: bound worktree does not exist: {path}"

        self.set_teammate_workdir(agent_name, path)
        return f"Working directory switched to worktree: {path}"

    def reset_teammate_workdir(self, agent_name: str) -> str:
        self.set_teammate_workdir(agent_name, self.workdir)
        return "Working directory reset to main workspace."

    def set_teammate_workdir(self, agent_name: str, workdir: Path) -> None:
        safe_name = self.bus.safe_agent_name(agent_name)
        with self.lock:
            state = self.active_teammates.setdefault(safe_name, {})
            state["workdir"] = str(workdir.resolve())

    def teammate_system_prompt(self, name: str, role: str) -> str:
        return (
            f"You are {name}, a teammate agent. Role: {role}.\n"
            "Use tools to complete assigned or auto-claimed tasks.\n"
            "When idle, the runtime will check your inbox and the persistent task board.\n"
            "If lead asks for a plan, call submit_plan and wait for plan_approval_response.\n"
            "If you claim a task bound to a worktree, bash/read_file/write_file run in that worktree.\n"
            "Send important progress and final results to lead with send_message."
        )

    def reinject_identity_if_needed(self, name: str, role: str, messages: list) -> None:
        if len(messages) > 3:
            return
        messages.insert(
            0,
            {
                "role": "user",
                "content": (
                    f"<identity>You are {name}. Role: {role}. "
                    "Continue as the same teammate.</identity>"
                ),
            },
        )

    def extract_latest_text(self, messages: list) -> str:
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            text = self.extract_text(message.get("content"))
            if text:
                return text
        return ""

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
