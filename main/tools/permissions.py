from pathlib import Path


class Permissions:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.deny_patterns = [
            "rm -rf /",
            "sudo",
            "shutdown",
            "reboot",
            "mkfs",
            "dd if=",
            "> /dev/sda",
        ]

    def check(self, block) -> str | None:
        denied = self.check_deny_list(block)
        if denied:
            return denied

        reason = self.check_rules(block)
        if reason and not self.ask_user(block, reason):
            return f"Permission denied: {reason}"

        return None

    def check_deny_list(self, block) -> str | None:
        if block.name != "bash":
            return None

        command = block.input.get("command", "")
        for pattern in self.deny_patterns:
            if pattern in command:
                return f"Blocked: '{pattern}' is on the deny list"

        return None

    def check_rules(self, block) -> str | None:
        if block.name in {"write_file", "edit_file"}:
            path = block.input.get("path", "")
            if self.escapes_workspace(path):
                return f"Writing outside workspace: {path}"

        if block.name == "bash":
            command = block.input.get("command", "")
            risky_parts = ["rm ", "del ", "rmdir ", "chmod 777", "> /etc/"]
            if any(part in command for part in risky_parts):
                return f"Potentially destructive command: {command}"

        return None

    def escapes_workspace(self, path: str) -> bool:
        try:
            target = (self.workdir / path).resolve()
            target.relative_to(self.workdir)
            return False
        except ValueError:
            return True

    def ask_user(self, block, reason: str) -> bool:
        print("\nPermission required")
        print(f"Tool: {block.name}")
        print(f"Reason: {reason}")
        print(f"Input: {dict(block.input)}")

        try:
            answer = input("Allow? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}
