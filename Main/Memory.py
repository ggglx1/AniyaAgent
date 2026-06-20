import os
import re
import time
from pathlib import Path

from StructuredOutput import StructuredOutputParser


class Memory:
    memory_types = {"user", "feedback", "project", "reference"}
    index_selection_schema = {
        "type": "array",
        "items": {"type": "integer"},
    }
    memory_items_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["name", "type", "description", "body"],
        },
    }

    def __init__(self, workdir: Path, client, model: str):
        self.workdir = workdir.resolve()
        self.client = client
        self.model = model
        self.output_parser = StructuredOutputParser()
        self.memory_dir = self.workdir / ".memory"
        self.memory_index = self.memory_dir / "MEMORY.md"
        self.match_mode = os.getenv("MEMORY_MATCH_MODE", "llm").strip().lower()
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def parse_frontmatter(self, text: str) -> tuple[dict, str]:
        if not text.startswith("---"):
            return {}, text

        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text

        meta = {}
        for line in parts[1].strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
        return meta, parts[2].strip()

    def slugify(self, value: str) -> str:
        lowered = value.lower().strip()
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", lowered)
        return slug.strip("-") or f"memory-{int(time.time())}"

    def write_memory_file(self, name: str, memory_type: str, description: str, body: str) -> Path:
        if memory_type not in self.memory_types:
            memory_type = "user"

        filepath = self.memory_dir / f"{self.slugify(name)}.md"
        filepath.write_text(
            (
                "---\n"
                f"name: {name}\n"
                f"description: {description}\n"
                f"type: {memory_type}\n"
                "---\n\n"
                f"{body}\n"
            ),
            encoding="utf-8",
        )
        self.rebuild_index()
        return filepath

    def rebuild_index(self) -> None:
        lines = []
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue

            raw = path.read_text(encoding="utf-8", errors="replace")
            meta, body = self.parse_frontmatter(raw)
            name = meta.get("name", path.stem)
            description = meta.get("description", body.splitlines()[0][:80] if body else "")
            lines.append(f"- [{name}]({path.name}) - {description}")

        self.memory_index.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def read_memory_index(self) -> str:
        if not self.memory_index.exists():
            return ""
        return self.memory_index.read_text(encoding="utf-8", errors="replace").strip()

    def read_memory_file(self, filename: str) -> str | None:
        path = (self.memory_dir / filename).resolve()
        try:
            path.relative_to(self.memory_dir.resolve())
        except ValueError:
            return None
        if not path.exists() or path.name == "MEMORY.md":
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def list_memory_files(self) -> list[dict]:
        result = []
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue

            raw = path.read_text(encoding="utf-8", errors="replace")
            meta, body = self.parse_frontmatter(raw)
            result.append(
                {
                    "filename": path.name,
                    "name": meta.get("name", path.stem),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "user"),
                    "body": body,
                }
            )
        return result

    def select_relevant_memories(self, messages: list, max_items: int = 5) -> list[str]:
        files = self.list_memory_files()
        if not files:
            return []

        recent = self.recent_user_text(messages)
        if not recent.strip():
            return []
        if self.match_mode in {"keyword", "grep", "local"}:
            return self.keyword_memory_match(files, recent, max_items)

        catalog = "\n".join(
            f"{index}: {item['name']} - {item['description']}"
            for index, item in enumerate(files)
        )
        prompt = (
            "Given the recent conversation and the memory catalog below, "
            "select the indices of memories that are clearly relevant. "
            "Return ONLY a JSON array of integers, e.g. [0, 3]. "
            "If none are relevant, return [].\n\n"
            f"Recent conversation:\n{recent[:2000]}\n\n"
            f"Memory catalog:\n{catalog}"
        )

        try:
            response = self.client.messages.create(
                task_type="memory_match",
                model=self.model,
                system="You select relevant memory records for an agent.",
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=200,
            )
            text = self.extract_text(response.content).strip()
            indices = self.output_parser.repair_json(
                self.client,
                model=self.model,
                text=text,
                schema=self.index_selection_schema,
                instruction=prompt,
                max_tokens=300,
            )
            selected = []
            for index in indices:
                if isinstance(index, int) and 0 <= index < len(files):
                    selected.append(files[index]["filename"])
                if len(selected) >= max_items:
                    break
            return selected
        except Exception:
            pass

        return self.keyword_memory_match(files, recent, max_items)

    def load_memories(self, messages: list) -> str:
        selected_files = self.select_relevant_memories(messages)
        if not selected_files:
            return ""

        parts = ["<relevant_memories>"]
        for filename in selected_files:
            content = self.read_memory_file(filename)
            if content:
                parts.append(content)
        parts.append("</relevant_memories>")
        return "\n\n".join(parts)

    def extract_memories(self, messages: list) -> None:
        dialogue = self.dialogue_text(messages[-10:])
        if not dialogue.strip():
            return

        existing = self.list_memory_files()
        existing_desc = (
            "\n".join(f"- {item['name']}: {item['description']}" for item in existing)
            if existing
            else "(none)"
        )
        prompt = (
            "Extract user preferences, constraints, or project facts from this dialogue.\n"
            "Return a JSON array. Each item: {name, type, description, body}.\n"
            "- name: short kebab-case identifier\n"
            "- type: one of user, feedback, project, reference\n"
            "- description: one-line summary for index lookup\n"
            "- body: full detail in markdown\n"
            "If nothing new or already covered by existing memories, return [].\n\n"
            f"Existing memories:\n{existing_desc}\n\n"
            f"Dialogue:\n{dialogue[:4000]}"
        )

        try:
            response = self.client.messages.create(
                task_type="memory_extract",
                model=self.model,
                system="You extract durable memories for a coding agent.",
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=800,
            )
            text = self.extract_text(response.content).strip()
            items = self.output_parser.repair_json(
                self.client,
                model=self.model,
                text=text,
                schema=self.memory_items_schema,
                instruction=prompt,
                max_tokens=1200,
            )
            count = 0
            for item in items:
                name = item.get("name", f"memory-{int(time.time())}")
                memory_type = item.get("type", "user")
                description = item.get("description", "")
                body = item.get("body", "")
                if description and body:
                    self.write_memory_file(name, memory_type, description, body)
                    count += 1

            if count:
                print(f"\n[Memory: extracted {count} new memories]")
        except Exception:
            pass

    def consolidate_memories(self, threshold: int = 10) -> None:
        files = self.list_memory_files()
        if len(files) < threshold:
            return

        catalog = "\n\n".join(
            f"## {item['filename']}\n"
            f"name: {item['name']}\n"
            f"description: {item['description']}\n"
            f"{item['body']}"
            for item in files
        )
        prompt = (
            "Consolidate the following memory files. Rules:\n"
            "1. Merge duplicates into one\n"
            "2. Remove outdated or contradicted memories\n"
            "3. Keep the total under 30 memories\n"
            "4. Preserve important user preferences above all\n"
            "Return a JSON array. Each item: {name, type, description, body}.\n\n"
            f"{catalog[:16000]}"
        )

        try:
            response = self.client.messages.create(
                task_type="memory_consolidate",
                model=self.model,
                system="You consolidate persistent memories for a coding agent.",
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=3000,
            )
            text = self.extract_text(response.content).strip()
            items = self.output_parser.repair_json(
                self.client,
                model=self.model,
                text=text,
                schema=self.memory_items_schema,
                instruction=prompt,
                max_tokens=3000,
            )
            for path in self.memory_dir.glob("*.md"):
                if path.name != "MEMORY.md":
                    path.unlink()

            for item in items:
                description = item.get("description", "")
                body = item.get("body", "")
                if description and body:
                    self.write_memory_file(
                        item.get("name", f"memory-{int(time.time())}"),
                        item.get("type", "user"),
                        description,
                        body,
                    )

            print(f"\n[Memory: consolidated {len(files)} -> {len(items)} memories]")
        except Exception:
            pass

    def system_section(self) -> str:
        index = self.read_memory_index()
        if not index:
            return (
                "No persistent memories yet. "
                "When the user says remember or gives a stable preference, extract it as memory."
            )
        return (
            "Memories available:\n"
            f"{index}\n"
            "Relevant memories are injected into the current user turn when useful."
        )

    def recent_user_text(self, messages: list) -> str:
        recent_texts = []
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            text = self.content_to_text(message.get("content", ""))
            if text:
                recent_texts.append(text)
            if len(recent_texts) >= 3:
                break
        return " ".join(reversed(recent_texts))

    def dialogue_text(self, messages: list) -> str:
        lines = []
        for message in messages:
            role = message.get("role", "?")
            text = self.content_to_text(message.get("content", ""))
            if text.strip():
                lines.append(f"{role}: {text}")
        return "\n".join(lines)

    def content_to_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)

        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_result":
                    parts.append(str(block.get("content", ""))[:1000])
                elif block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            elif getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "")))
        return " ".join(part for part in parts if part)

    def extract_text(self, content) -> str:
        return self.content_to_text(content)

    def keyword_memory_match(self, files: list[dict], recent: str, max_items: int) -> list[str]:
        keywords = [word.lower() for word in re.findall(r"[\w\u4e00-\u9fff]+", recent) if len(word) > 3]
        selected = []
        for item in files:
            text = f"{item['name']} {item['description']}".lower()
            if any(keyword in text for keyword in keywords):
                selected.append(item["filename"])
            if len(selected) >= max_items:
                break
        return selected
