import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from StructuredOutput import StructuredOutputParser


class Memory:
    memory_types = {"user", "feedback", "project", "reference", "procedure"}
    memory_scopes = {"global", "project", "session", "agent"}
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
                "scope": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
                "source": {"type": "string"},
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {"type": "integer"},
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
        self.metadata_index_file = self.memory_dir / "metadata_index.json"
        self.entity_index_file = self.memory_dir / "entity_index.json"
        self.history_file = self.memory_dir / "history.jsonl"
        self.metrics_file = self.memory_dir / "metrics.jsonl"
        self.match_mode = os.getenv("MEMORY_MATCH_MODE", "local").strip().lower()
        self.query_cache_size = int(os.getenv("MEMORY_QUERY_CACHE_SIZE", "100"))
        self.file_cache_size = int(os.getenv("MEMORY_FILE_CACHE_SIZE", "300"))
        self.query_cache = OrderedDict()
        self.file_cache = OrderedDict()
        self.consolidation_lock = threading.Lock()
        self.consolidation_running = False
        self.extract_lock = threading.Lock()
        self.extract_running = False
        self.memory_match_lock = threading.Lock()
        self.memory_match_running = False
        self.memory_dirty = False
        self.turns_since_extract = 0
        self.turns_since_memory_match = 0
        self.last_extract_at = 0.0
        self.last_consolidate_at = 0.0
        self.last_memory_match_at = 0.0
        self.extract_min_round_interval = int(os.getenv("MEMORY_EXTRACT_MIN_ROUND_INTERVAL", "3"))
        self.extract_min_seconds_interval = int(os.getenv("MEMORY_EXTRACT_MIN_SECONDS_INTERVAL", "60"))
        self.consolidate_min_files = int(os.getenv("MEMORY_CONSOLIDATE_MIN_FILES", "20"))
        self.consolidate_min_seconds_interval = int(
            os.getenv("MEMORY_CONSOLIDATE_MIN_SECONDS_INTERVAL", "300")
        )
        self.entity_index_enabled = os.getenv("MEMORY_ENTITY_INDEX_ENABLED", "true").strip().lower() != "false"
        self.metrics_enabled = os.getenv("MEMORY_METRICS_ENABLED", "true").strip().lower() != "false"
        self.async_memory_match_enabled = (
            os.getenv("MEMORY_LLM_MATCH_ASYNC_ENABLED", "true").strip().lower() != "false"
        )
        self.background_extract_enabled = (
            os.getenv("MEMORY_BACKGROUND_EXTRACT_ENABLED", "true").strip().lower() != "false"
        )
        self.memory_match_min_round_interval = int(os.getenv("MEMORY_MATCH_MIN_ROUND_INTERVAL", "4"))
        self.memory_match_min_seconds_interval = int(os.getenv("MEMORY_MATCH_MIN_SECONDS_INTERVAL", "45"))
        self.memory_match_confidence_threshold = int(os.getenv("MEMORY_MATCH_LOCAL_CONFIDENCE_THRESHOLD", "80"))
        self.memory_match_schedule_delay = float(os.getenv("MEMORY_MATCH_SCHEDULE_DELAY_SECONDS", "2"))
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

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def new_memory_id(self) -> str:
        return f"mem_{uuid.uuid4().hex[:12]}"

    def new_event_id(self) -> str:
        return f"evt_{uuid.uuid4().hex[:12]}"

    def stable_id_for_path(self, path: Path) -> str:
        digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
        return f"mem_{digest}"

    def safe_int(self, value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def record_history(
        self,
        memory_id: str,
        event: str,
        old_memory: str | None = None,
        new_memory: str | None = None,
        source: str = "",
    ) -> None:
        record = {
            "id": self.new_event_id(),
            "memory_id": memory_id,
            "event": event,
            "old_memory": old_memory,
            "new_memory": new_memory,
            "source": source,
            "created_at": self.now_iso(),
        }
        with self.history_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record_metric(self, event: str, payload: dict | None = None) -> None:
        if not self.metrics_enabled:
            return
        record = {
            "event": event,
            "payload": payload or {},
            "created_at": self.now_iso(),
        }
        try:
            with self.metrics_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def write_memory_file(
        self,
        name: str,
        memory_type: str,
        description: str,
        body: str,
        mark_dirty: bool = True,
        memory_id: str = "",
        scope: str = "project",
        source: str = "",
        confidence: int = 80,
        event_type: str = "ADD",
    ) -> Path:
        if memory_type not in self.memory_types:
            memory_type = "user"
        if scope not in self.memory_scopes:
            scope = "project"

        filepath = self.memory_dir / f"{self.slugify(name)}.md"
        old_content = filepath.read_text(encoding="utf-8", errors="replace") if filepath.exists() else ""
        old_meta, _ = self.parse_frontmatter(old_content) if old_content else ({}, "")
        now = self.now_iso()
        stable_id = memory_id or old_meta.get("id") or self.new_memory_id()
        created_at = old_meta.get("created_at") or now
        use_count = old_meta.get("use_count", "0")
        last_used_at = old_meta.get("last_used_at", "")
        filepath.write_text(
            (
                "---\n"
                f"id: {stable_id}\n"
                f"name: {name}\n"
                f"description: {description}\n"
                f"type: {memory_type}\n"
                f"scope: {scope}\n"
                f"source: {source}\n"
                f"created_at: {created_at}\n"
                f"updated_at: {now}\n"
                f"last_used_at: {last_used_at}\n"
                f"use_count: {use_count}\n"
                f"confidence: {confidence}\n"
                "---\n\n"
                f"{body}\n"
            ),
            encoding="utf-8",
        )
        history_event = event_type if event_type != "ADD" else ("UPDATE" if old_content else "ADD")
        self.record_history(
            stable_id,
            history_event,
            old_memory=old_content or None,
            new_memory=body,
            source=source,
        )
        self.rebuild_index()
        self.clear_caches()
        if mark_dirty:
            self.memory_dirty = True
        return filepath

    def rebuild_index(self) -> None:
        lines = []
        metadata_index = {}
        entity_index = {}
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue

            raw = path.read_text(encoding="utf-8", errors="replace")
            meta, body = self.parse_frontmatter(raw)
            name = meta.get("name", path.stem)
            description = meta.get("description", body.splitlines()[0][:80] if body else "")
            lines.append(f"- [{name}]({path.name}) - {description}")
            stat = path.stat()
            memory_type = meta.get("type", "user")
            scope = meta.get("scope", "project")
            memory_id = meta.get("id") or self.stable_id_for_path(path)
            keywords = sorted(self.extract_keywords(f"{path.stem} {name} {description} {memory_type} {scope}"))
            entities = sorted(self.extract_entities(f"{path.stem} {name} {description} {body[:1200]}"))
            metadata_index[path.name] = {
                "path": str(path),
                "filename": path.name,
                "id": memory_id,
                "name": name,
                "description": description,
                "type": memory_type,
                "scope": scope,
                "source": meta.get("source", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "last_used_at": meta.get("last_used_at", ""),
                "use_count": self.safe_int(meta.get("use_count", 0)),
                "confidence": self.safe_int(meta.get("confidence", 80)),
                "keywords": keywords,
                "entities": entities,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
            for entity in entities:
                entry = entity_index.setdefault(
                    entity,
                    {
                        "entity": entity,
                        "linked_memory_ids": [],
                        "linked_filenames": [],
                    },
                )
                if memory_id not in entry["linked_memory_ids"]:
                    entry["linked_memory_ids"].append(memory_id)
                if path.name not in entry["linked_filenames"]:
                    entry["linked_filenames"].append(path.name)

        self.memory_index.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        self.metadata_index_file.write_text(
            json.dumps(metadata_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if self.entity_index_enabled:
            self.entity_index_file.write_text(
                json.dumps(entity_index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self.clear_caches(query_only=True)

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
        stat = path.stat()
        cache_key = f"{path}|{stat.st_mtime}|{stat.st_size}"
        cached = self.file_cache_get(cache_key)
        if cached is not None:
            return cached

        content = path.read_text(encoding="utf-8", errors="replace")
        self.file_cache_put(cache_key, content)
        return content

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
                    "id": meta.get("id") or self.stable_id_for_path(path),
                    "name": meta.get("name", path.stem),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "user"),
                    "scope": meta.get("scope", "project"),
                    "source": meta.get("source", ""),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "last_used_at": meta.get("last_used_at", ""),
                    "use_count": self.safe_int(meta.get("use_count", 0)),
                    "confidence": self.safe_int(meta.get("confidence", 80)),
                    "body": body,
                }
            )
        return result

    def select_relevant_memories(self, messages: list, max_items: int = 5) -> list[str]:
        started = time.perf_counter()
        metadata_started = time.perf_counter()
        files = self.list_memory_metadata()
        metadata_ms = round((time.perf_counter() - metadata_started) * 1000, 2)
        if not files:
            self.record_metric(
                "memory.recall",
                {
                    "metadata_load_ms": metadata_ms,
                    "selected_memory_count": 0,
                    "reason": "empty_index",
                    "total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return []

        recent = self.recent_user_text(messages)
        if not recent.strip():
            self.record_metric(
                "memory.recall",
                {
                    "metadata_load_ms": metadata_ms,
                    "selected_memory_count": 0,
                    "reason": "empty_query",
                    "total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return []

        query_cache_key = self.query_cache_key(recent)
        cached = self.query_cache_get(query_cache_key)
        if cached is not None:
            self.record_metric(
                "memory.recall",
                {
                    "metadata_load_ms": metadata_ms,
                    "query_cache_hit": True,
                    "selected_memory_count": len(cached[:max_items]),
                    "total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return cached[:max_items]

        local_started = time.perf_counter()
        selected, top_score = self.local_memory_match(files, recent, max_items, include_score=True)
        local_ms = round((time.perf_counter() - local_started) * 1000, 2)
        self.query_cache_put(query_cache_key, selected)
        scheduled, skipped_reason = self.maybe_schedule_memory_match(
            messages,
            recent,
            query_cache_key,
            selected,
            top_score,
            max_items,
        )
        self.record_metric(
            "memory.recall",
            {
                "metadata_load_ms": metadata_ms,
                "local_recall_ms": local_ms,
                "query_cache_hit": False,
                "local_top_score": top_score,
                "selected_memory_count": len(selected),
                "llm_match_scheduled": scheduled,
                "llm_match_skipped_reason": skipped_reason,
                "total_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return selected

    def llm_select_relevant_memories(self, messages: list, max_items: int = 5) -> list[str]:
        files = self.list_memory_metadata()
        if not files:
            return []

        recent = self.recent_user_text(messages)
        if not recent.strip():
            return []
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

    def maybe_schedule_memory_match(
        self,
        messages: list,
        recent: str,
        query_cache_key: str,
        local_selected: list[str],
        local_top_score: int,
        max_items: int,
    ) -> tuple[bool, str]:
        if not self.async_memory_match_enabled:
            return False, "disabled"

        self.turns_since_memory_match += 1
        should_schedule, skipped_reason = self.should_schedule_memory_match(recent, local_selected, local_top_score)
        if not should_schedule:
            return False, skipped_reason

        with self.memory_match_lock:
            if self.memory_match_running:
                return False, "already_running"
            self.memory_match_running = True
            self.last_memory_match_at = time.time()
            self.turns_since_memory_match = 0

        snapshot = [dict(message) for message in messages[-8:]]
        timer = threading.Timer(
            self.memory_match_schedule_delay,
            self._memory_match_worker,
            args=(snapshot, query_cache_key, max_items),
        )
        timer.daemon = True
        timer.start()
        return True, ""

    def should_schedule_memory_match(
        self,
        recent: str,
        local_selected: list[str],
        local_top_score: int,
    ) -> tuple[bool, str]:
        elapsed = time.time() - self.last_memory_match_at if self.last_memory_match_at else None
        if elapsed is not None and elapsed < self.memory_match_min_seconds_interval:
            return False, "seconds_interval"
        if self.turns_since_memory_match < self.memory_match_min_round_interval:
            return False, "round_interval"
        if local_top_score < self.memory_match_confidence_threshold:
            return True, ""
        if not local_selected and self.has_memory_recall_signal(recent):
            return True, ""
        return False, "local_confident"

    def _memory_match_worker(self, messages: list, query_cache_key: str, max_items: int) -> None:
        started = time.perf_counter()
        selected = []
        try:
            selected = self.llm_select_relevant_memories(messages, max_items=max_items)
            if selected:
                self.query_cache_put(query_cache_key, selected)
        except Exception:
            pass
        finally:
            self.record_metric(
                "memory.llm_match",
                {
                    "selected_memory_count": len(selected),
                    "total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            with self.memory_match_lock:
                self.memory_match_running = False

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
            self.record_metric("memory.extract", {"scheduled": False, "skipped_reason": "empty_dialogue"})
            return
        if not self.should_extract_memories(dialogue):
            self.record_metric("memory.extract", {"scheduled": False, "skipped_reason": "rate_limited"})
            return
        self.last_extract_at = time.time()
        self.turns_since_extract = 0

        if not self.background_extract_enabled:
            self.record_metric("memory.extract", {"scheduled": False, "mode": "sync"})
            self._extract_memories_sync(dialogue)
            return

        with self.extract_lock:
            if self.extract_running:
                self.record_metric("memory.extract", {"scheduled": False, "skipped_reason": "already_running"})
                return
            self.extract_running = True

        thread = threading.Thread(
            target=self._extract_memories_worker,
            args=(dialogue,),
            daemon=True,
            name="memory-extract",
        )
        thread.start()
        self.record_metric("memory.extract", {"scheduled": True, "mode": "background"})

    def _extract_memories_worker(self, dialogue: str) -> None:
        started = time.perf_counter()
        try:
            self._extract_memories_sync(dialogue)
        except Exception as exc:
            print(f"\n[Memory: async extract failed: {type(exc).__name__}: {exc}]")
        finally:
            self.record_metric(
                "memory.extract.completed",
                {"total_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
            with self.extract_lock:
                self.extract_running = False

    def _extract_memories_sync(self, dialogue: str) -> None:
        existing = self.list_memory_files()
        existing_desc = (
            "\n".join(f"- {item['name']}: {item['description']}" for item in existing)
            if existing
            else "(none)"
        )
        prompt = (
            "Extract durable atomic memories for a coding agent.\n"
            "Return small facts, not broad summaries. Prefer one memory per stable preference, "
            "project fact, recurring instruction, or long-lived constraint.\n"
            "Return a JSON array. Each item: {name, type, scope, description, body, source, confidence}.\n"
            "- name: short kebab-case identifier\n"
            "- type: one of user, feedback, project, reference, procedure\n"
            "- scope: one of global, project, session, agent\n"
            "- description: one-line summary for index lookup\n"
            "- body: one durable fact in markdown, not the whole dialogue\n"
            "- source: short note about where this fact came from\n"
            "- confidence: integer 0-100\n"
            "If nothing new or already covered by existing memories, return [].\n\n"
            "Extract:\n"
            "- long-term user preferences\n"
            "- project facts\n"
            "- repository conventions\n"
            "- explicit long-term instructions\n"
            "- repeated feedback\n\n"
            "Do not extract:\n"
            "- temporary questions\n"
            "- one-off tool outputs\n"
            "- obviously stale facts\n"
            "- casual conversation without long-term value\n\n"
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
                scope = item.get("scope", "project")
                description = item.get("description", "")
                body = item.get("body", "")
                source = item.get("source", "memory_extract")
                confidence = self.safe_int(item.get("confidence", 80))
                if description and body:
                    self.write_memory_file(
                        name,
                        memory_type,
                        description,
                        body,
                        scope=scope,
                        source=source,
                        confidence=confidence,
                    )
                    count += 1

            if count:
                print(f"\n[Memory: extracted {count} new memories]")
            self.record_metric("memory.extract.result", {"extracted_count": count})
        except Exception:
            self.record_metric("memory.extract.result", {"extracted_count": 0, "error": True})
            pass

    def consolidate_memories(self, threshold: int = 10) -> None:
        threshold = max(threshold, self.consolidate_min_files)
        files = self.list_memory_files()
        if len(files) < threshold:
            self.record_metric(
                "memory.consolidate",
                {"scheduled": False, "skipped_reason": "below_threshold", "memory_count": len(files), "threshold": threshold},
            )
            return
        if not self.memory_dirty:
            self.record_metric(
                "memory.consolidate",
                {"scheduled": False, "skipped_reason": "not_dirty", "memory_count": len(files), "threshold": threshold},
            )
            return
        if time.time() - self.last_consolidate_at < self.consolidate_min_seconds_interval:
            self.record_metric(
                "memory.consolidate",
                {"scheduled": False, "skipped_reason": "seconds_interval", "memory_count": len(files), "threshold": threshold},
            )
            return

        with self.consolidation_lock:
            if self.consolidation_running:
                print("\n[Memory: consolidation already running]")
                self.record_metric(
                    "memory.consolidate",
                    {"scheduled": False, "skipped_reason": "already_running", "memory_count": len(files), "threshold": threshold},
                )
                return
            self.consolidation_running = True

        thread = threading.Thread(
            target=self._consolidate_memories_worker,
            args=(threshold,),
            daemon=True,
            name="memory-consolidate",
        )
        thread.start()
        print(f"\n[Memory: consolidation scheduled for {len(files)} memories]")
        self.record_metric(
            "memory.consolidate",
            {"scheduled": True, "memory_count": len(files), "threshold": threshold},
        )

    def _consolidate_memories_worker(self, threshold: int) -> None:
        started = time.perf_counter()
        try:
            self._consolidate_memories_sync(threshold)
        except Exception as exc:
            print(f"\n[Memory: async consolidation failed: {type(exc).__name__}: {exc}]")
        finally:
            self.record_metric(
                "memory.consolidate.completed",
                {"total_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
            with self.consolidation_lock:
                self.consolidation_running = False

    def _consolidate_memories_sync(self, threshold: int = 10) -> None:
        files = self.list_memory_files()
        if len(files) < threshold:
            return

        catalog = "\n\n".join(
            f"## {item['filename']}\n"
            f"id: {item['id']}\n"
            f"name: {item['name']}\n"
            f"type: {item['type']}\n"
            f"scope: {item['scope']}\n"
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
            "Return a JSON array. Each item: {name, type, scope, description, body, source_ids, confidence}.\n"
            "source_ids must contain the ids of old memories used to create the consolidated memory.\n\n"
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
            if not items:
                return

            existing_paths = {}
            for path in self.memory_dir.glob("*.md"):
                if path.name == "MEMORY.md":
                    continue
                old_content = path.read_text(encoding="utf-8", errors="replace")
                old_meta, _ = self.parse_frontmatter(old_content)
                old_id = old_meta.get("id") or self.stable_id_for_path(path)
                existing_paths[old_id] = {
                    "path": path,
                    "content": old_content,
                }

            kept_paths = set()
            merge_source_ids = set()
            for item in items:
                description = item.get("description", "")
                body = item.get("body", "")
                if description and body:
                    raw_source_ids = item.get("source_ids") or []
                    source_ids = [
                        memory_id
                        for memory_id in raw_source_ids
                        if isinstance(memory_id, str) and memory_id in existing_paths
                    ]
                    primary_id = source_ids[0] if source_ids else ""
                    merge_source_ids.update(source_ids)
                    path = self.write_memory_file(
                        item.get("name", f"memory-{int(time.time())}"),
                        item.get("type", "user"),
                        description,
                        body,
                        mark_dirty=False,
                        memory_id=primary_id,
                        scope=item.get("scope", "project"),
                        source=f"memory_consolidate:{','.join(source_ids)}" if source_ids else "memory_consolidate",
                        confidence=self.safe_int(item.get("confidence", 80)),
                        event_type="CONSOLIDATE",
                    )
                    kept_paths.add(path.resolve())

            for memory_id, item in existing_paths.items():
                path = item["path"]
                if path.resolve() in kept_paths:
                    continue
                self.record_history(
                    memory_id,
                    "MERGE" if memory_id in merge_source_ids else "DELETE",
                    old_memory=item["content"],
                    new_memory=None,
                    source="memory_consolidate",
                )
                path.unlink()

            self.rebuild_index()
            self.memory_dirty = False
            self.last_consolidate_at = time.time()
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

    def should_extract_memories(self, dialogue: str) -> bool:
        self.turns_since_extract += 1

        elapsed = time.time() - self.last_extract_at if self.last_extract_at else None
        if elapsed is not None and elapsed < self.extract_min_seconds_interval:
            return False

        if self.has_high_value_memory_signal(dialogue):
            return True

        return self.turns_since_extract >= self.extract_min_round_interval

    def has_high_value_memory_signal(self, text: str) -> bool:
        lowered = text.lower()
        patterns = (
            "remember",
            "记住",
            "以后",
            "偏好",
            "习惯",
            "约定",
            "长期",
            "不要忘",
            "我希望",
            "我要求",
            "always",
            "never",
            "prefer",
            "preference",
            "constraint",
            "project fact",
        )
        return any(pattern in lowered for pattern in patterns)

    def has_memory_recall_signal(self, text: str) -> bool:
        lowered = text.lower()
        patterns = (
            "remember",
            "记得",
            "之前",
            "上次",
            "以前",
            "偏好",
            "约定",
            "memory",
            "previous",
            "before",
            "last time",
            "preference",
        )
        return any(pattern in lowered for pattern in patterns)

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

    def list_memory_metadata(self) -> list[dict]:
        index = self.load_metadata_index()
        return [index[key] for key in sorted(index)]

    def load_metadata_index(self) -> dict:
        if not self.metadata_index_file.exists() or self.metadata_index_is_stale():
            self.rebuild_index()

        try:
            return json.loads(self.metadata_index_file.read_text(encoding="utf-8"))
        except Exception:
            self.rebuild_index()
            return json.loads(self.metadata_index_file.read_text(encoding="utf-8"))

    def metadata_index_is_stale(self) -> bool:
        if not self.metadata_index_file.exists():
            return True
        if self.entity_index_enabled and not self.entity_index_file.exists():
            return True

        try:
            index = json.loads(self.metadata_index_file.read_text(encoding="utf-8"))
        except Exception:
            return True

        current_files = {
            path.name: path
            for path in self.memory_dir.glob("*.md")
            if path.name != "MEMORY.md"
        }
        if set(index.keys()) != set(current_files.keys()):
            return True

        for filename, path in current_files.items():
            item = index.get(filename, {})
            stat = path.stat()
            if item.get("mtime") != stat.st_mtime or item.get("size") != stat.st_size:
                return True
        return False

    def load_entity_index(self) -> dict:
        if not self.entity_index_enabled:
            return {}
        if not self.entity_index_file.exists() or self.metadata_index_is_stale():
            self.rebuild_index()
        try:
            return json.loads(self.entity_index_file.read_text(encoding="utf-8"))
        except Exception:
            self.rebuild_index()
            return json.loads(self.entity_index_file.read_text(encoding="utf-8"))

    def local_memory_match(
        self,
        files: list[dict],
        recent: str,
        max_items: int,
        include_score: bool = False,
    ):
        query = self.normalize_query(recent)
        query_terms = self.extract_keywords(query)
        query_entities = self.extract_entities(recent)
        entity_index = self.load_entity_index()
        entity_filename_hits = self.entity_filename_hits(query_entities, entity_index)
        if not query_terms and not query_entities:
            return ([], 0) if include_score else []

        scored = []
        for item in files:
            score = self.metadata_score(item, query, query_terms, query_entities, entity_filename_hits)
            scored.append((score, item))

        candidates = [item for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0]

        # Read only top metadata candidates for a light body-prefix boost.
        rescored = []
        for item in candidates[: max(max_items * 3, 10)]:
            score = self.metadata_score(item, query, query_terms, query_entities, entity_filename_hits)
            content = self.read_memory_file(item["filename"]) or ""
            prefix = content[:1200].lower()
            if any(term in prefix for term in query_terms):
                score += 10
            rescored.append((score, item))

        sorted_items = sorted(rescored, key=lambda pair: pair[0], reverse=True)
        selected = [
            item["filename"]
            for score, item in sorted_items[:max_items]
            if score > 0
        ]
        top_score = sorted_items[0][0] if sorted_items else 0
        return (selected, top_score) if include_score else selected

    def entity_filename_hits(self, query_entities: set[str], entity_index: dict) -> set[str]:
        hits = set()
        for entity in query_entities:
            entry = entity_index.get(entity)
            if not entry:
                continue
            hits.update(entry.get("linked_filenames", []))
        return hits

    def metadata_score(
        self,
        item: dict,
        query: str,
        query_terms: set[str],
        query_entities: set[str] | None = None,
        entity_filename_hits: set[str] | None = None,
    ) -> int:
        filename = str(item.get("filename", "")).lower()
        name = str(item.get("name", "")).lower()
        description = str(item.get("description", "")).lower()
        memory_type = str(item.get("type", "")).lower()
        keywords = set(item.get("keywords", []))
        item_entities = set(item.get("entities", []))
        query_entities = query_entities or set()
        entity_filename_hits = entity_filename_hits or set()
        score = 0

        if query and (query == filename or query == name):
            score += 100
        if any(term == filename or term == name for term in query_terms):
            score += 80
        if any(term in filename or term in name for term in query_terms):
            score += 60
        if any(term in description for term in query_terms):
            score += 40
        if query_terms & keywords:
            score += 30
        if memory_type in query_terms:
            score += 20
        entity_overlap = query_entities & item_entities
        if entity_overlap:
            score += min(80, 40 + 10 * len(entity_overlap))
        if item.get("filename") in entity_filename_hits:
            score += 40
        score += min(20, self.safe_int(item.get("use_count", 0)) * 2)

        mtime = float(item.get("mtime", 0) or 0)
        age_seconds = max(time.time() - mtime, 0)
        if age_seconds < 3600:
            score += 20
        elif age_seconds < 86400:
            score += 10
        elif age_seconds < 604800:
            score += 5
        return score

    def query_cache_key(self, recent: str) -> str:
        normalized = self.normalize_query(recent)
        index_hash = self.metadata_index_hash()
        return f"{index_hash}:{normalized}"

    def metadata_index_hash(self) -> str:
        index = self.load_metadata_index()
        payload = json.dumps(index, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def normalize_query(self, text: str) -> str:
        return " ".join(text.lower().split())[:2000]

    def extract_keywords(self, text: str) -> set[str]:
        return {
            word.lower()
            for word in re.findall(r"[\w\u4e00-\u9fff]+", text)
            if len(word) > 2
        }

    def extract_entities(self, text: str) -> set[str]:
        entities = set()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.:/#-]{2,}", text):
            normalized = token.strip("._-:/#").lower()
            if not normalized or normalized in self.stop_entities():
                continue
            if (
                any(char.isupper() for char in token[1:])
                or any(char.isdigit() for char in token)
                or any(char in token for char in "._:/#-")
                or len(normalized) >= 8
            ):
                entities.add(normalized)

        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_/-]{2,}", text):
            if not re.search(r"[\u4e00-\u9fff]", token):
                continue
            normalized = token.lower()
            if len(normalized) >= 2:
                entities.add(normalized)
        return entities

    def stop_entities(self) -> set[str]:
        return {
            "the",
            "and",
            "for",
            "with",
            "from",
            "this",
            "that",
            "return",
            "memory",
            "user",
            "project",
            "description",
            "source",
            "confidence",
        }

    def query_cache_get(self, key: str) -> list[str] | None:
        value = self.query_cache.get(key)
        if value is None:
            return None
        self.query_cache.move_to_end(key)
        return list(value)

    def query_cache_put(self, key: str, value: list[str]) -> None:
        self.query_cache[key] = list(value)
        self.query_cache.move_to_end(key)
        while len(self.query_cache) > self.query_cache_size:
            self.query_cache.popitem(last=False)

    def file_cache_get(self, key: str) -> str | None:
        value = self.file_cache.get(key)
        if value is None:
            return None
        self.file_cache.move_to_end(key)
        return value

    def file_cache_put(self, key: str, value: str) -> None:
        self.file_cache[key] = value
        self.file_cache.move_to_end(key)
        while len(self.file_cache) > self.file_cache_size:
            self.file_cache.popitem(last=False)

    def clear_caches(self, query_only: bool = False) -> None:
        self.query_cache.clear()
        if not query_only:
            self.file_cache.clear()
