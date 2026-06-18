from pathlib import Path


class Skills:
    def __init__(self, workdir: Path, skills_dir: str = "skills"):
        self.workdir = workdir.resolve()
        self.skills_dir = self.workdir / skills_dir
        self.registry = {}
        self.scan()

    def scan(self) -> None:
        self.registry.clear()
        if not self.skills_dir.exists():
            return

        for directory in sorted(self.skills_dir.iterdir()):
            if not directory.is_dir():
                continue

            manifest = directory / "SKILL.md"
            if not manifest.exists():
                continue

            raw = manifest.read_text(encoding="utf-8", errors="replace")
            meta, body = self.parse_frontmatter(raw)
            name = meta.get("name") or directory.name
            description = meta.get("description") or self.first_heading(body) or "No description"
            skill = {
                "name": name,
                "description": description,
                "content": raw,
            }
            self.registry[name] = skill
            self.registry.setdefault(directory.name, skill)

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

    def first_heading(self, body: str) -> str:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return ""

    def catalog_text(self) -> str:
        if not self.registry:
            return "(no skills found)"

        seen = set()
        lines = []
        for skill in self.registry.values():
            name = skill["name"]
            if name in seen:
                continue
            seen.add(name)
            lines.append(f"- {name}: {skill['description']}")
        return "\n".join(lines)

    def system_section(self) -> str:
        return (
            "Skills available:\n"
            f"{self.catalog_text()}\n"
            "Use load_skill(name) to load full skill instructions only when needed."
        )

    def load_skill(self, name: str) -> str:
        skill = self.registry.get(name)
        if skill is None:
            return f"Skill not found: {name}"
        return skill["content"]
