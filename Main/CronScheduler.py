import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = True
    target_channel: str = "cron"
    conversation_id: str = "scheduled"
    user_id: str = "scheduler"


class CronScheduler:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.storage_path = self.workdir / ".scheduled_tasks.json"
        self.jobs = {}
        self.queue = []
        self.last_fired = {}
        self.lock = threading.Lock()
        self.started = False
        self.load_durable_jobs()

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

    def schedule(
        self,
        cron: str,
        prompt: str,
        recurring: bool = True,
        durable: bool = True,
        target_channel: str = "cron",
        conversation_id: str = "scheduled",
        user_id: str = "scheduler",
    ) -> str:
        error = self.validate_cron(cron)
        if error:
            return f"Error: {error}"

        job = CronJob(
            id=self.new_job_id(),
            cron=cron,
            prompt=prompt,
            recurring=recurring,
            durable=durable,
            target_channel=target_channel or "cron",
            conversation_id=conversation_id or "scheduled",
            user_id=user_id or "scheduler",
        )
        with self.lock:
            self.jobs[job.id] = job
            if durable:
                self.save_durable_jobs()
        return f"Scheduled {job.id}: {cron} -> {prompt} target_channel={job.target_channel}"

    def list_jobs(self) -> str:
        with self.lock:
            jobs = list(self.jobs.values())
        if not jobs:
            return "No scheduled cron jobs."
        return "\n".join(
            f"{job.id}: {job.cron} recurring={job.recurring} durable={job.durable} "
            f"target_channel={job.target_channel} conversation={job.conversation_id} prompt={job.prompt}"
            for job in jobs
        )

    def cancel(self, job_id: str) -> str:
        with self.lock:
            if job_id not in self.jobs:
                return f"Error: cron job not found: {job_id}"
            self.jobs.pop(job_id)
            self.last_fired.pop(job_id, None)
            self.save_durable_jobs()
        return f"Cancelled {job_id}"

    def consume_queue(self) -> list[CronJob]:
        with self.lock:
            jobs = list(self.queue)
            self.queue.clear()
        return jobs

    def scheduler_loop(self) -> None:
        while True:
            time.sleep(1)
            self.tick(datetime.now())

    def tick(self, now: datetime) -> None:
        minute_marker = now.strftime("%Y-%m-%d %H:%M")
        remove_ids = []
        with self.lock:
            jobs = list(self.jobs.values())

            for job in jobs:
                try:
                    if not self.cron_matches(job.cron, now):
                        continue
                    if self.last_fired.get(job.id) == minute_marker:
                        continue

                    self.queue.append(job)
                    self.last_fired[job.id] = minute_marker
                    if not job.recurring:
                        remove_ids.append(job.id)
                except Exception as exc:
                    print(f"[cron error] {job.id}: {exc}")

            for job_id in remove_ids:
                self.jobs.pop(job_id, None)
                self.last_fired.pop(job_id, None)
            if remove_ids:
                self.save_durable_jobs()

    def cron_matches(self, cron_expr: str, dt: datetime) -> bool:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return False

        minute, hour, dom, month, dow = fields
        dow_value = (dt.weekday() + 1) % 7
        if not self.field_matches(minute, dt.minute):
            return False
        if not self.field_matches(hour, dt.hour):
            return False
        if not self.field_matches(month, dt.month):
            return False

        dom_ok = self.field_matches(dom, dt.day)
        dow_ok = self.field_matches(dow, dow_value)
        if dom == "*" and dow == "*":
            return True
        if dom == "*":
            return dow_ok
        if dow == "*":
            return dom_ok
        return dom_ok or dow_ok

    def field_matches(self, expression: str, value: int) -> bool:
        for part in expression.split(","):
            if self.part_matches(part, value):
                return True
        return False

    def part_matches(self, part: str, value: int) -> bool:
        if part == "*":
            return True
        if part.startswith("*/"):
            step = int(part[2:])
            return step > 0 and value % step == 0
        if "-" in part:
            start, end = part.split("-", 1)
            return int(start) <= value <= int(end)
        return int(part) == value

    def validate_cron(self, cron_expr: str) -> str | None:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return "Cron expression must have five fields: minute hour day month weekday"

        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
        for field, (minimum, maximum) in zip(fields, ranges):
            error = self.validate_field(field, minimum, maximum)
            if error:
                return error
        return None

    def validate_field(self, field: str, minimum: int, maximum: int) -> str | None:
        try:
            for part in field.split(","):
                if part == "*":
                    continue
                if part.startswith("*/"):
                    step = int(part[2:])
                    if step <= 0:
                        return f"Invalid cron step: {part}"
                    continue
                if "-" in part:
                    start, end = part.split("-", 1)
                    start_value = int(start)
                    end_value = int(end)
                    if start_value > end_value:
                        return f"Invalid cron range: {part}"
                    if start_value < minimum or end_value > maximum:
                        return f"Cron value out of range: {part}"
                    continue
                value = int(part)
                if value < minimum or value > maximum:
                    return f"Cron value out of range: {part}"
        except ValueError:
            return f"Invalid cron field: {field}"
        return None

    def load_durable_jobs(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            items = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in items:
                job = CronJob(**item)
                if self.validate_cron(job.cron) is None:
                    self.jobs[job.id] = job
        except Exception as exc:
            print(f"[cron load error] {exc}")

    def save_durable_jobs(self) -> None:
        jobs = [asdict(job) for job in self.jobs.values() if job.durable]
        self.storage_path.write_text(
            json.dumps(jobs, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def new_job_id(self) -> str:
        return f"cron_{int(time.time())}"
