import json
import copy
import time
from pathlib import Path


class ContextCompactor:
    def __init__(
        self,
        workdir: Path,
        client,
        model: str,
        context_limit: int = 50_000,
        keep_recent_tool_results: int = 3,
        persist_threshold: int = 30_000,
    ):
        self.workdir = workdir.resolve()
        self.client = client
        self.model = model
        self.context_limit = context_limit
        self.keep_recent_tool_results = keep_recent_tool_results
        self.persist_threshold = persist_threshold
        self.transcript_dir = self.workdir / ".transcripts"
        self.tool_results_dir = self.workdir / ".task_outputs" / "tool-results"
        self.compacted_results_dir = self.workdir / ".task_outputs" / "compacted-tool-results"

    def preprocess(self, messages: list) -> list:
        messages = self.tool_result_budget(messages)
        messages = self.snip_compact(messages)
        messages = self.micro_compact(messages)
        return messages

    def should_auto_compact(self, messages: list) -> bool:
        return self.estimate_size(messages) > self.context_limit

    def estimate_size(self, messages: list) -> int:
        return len(str(messages))

    def snip_compact(self, messages: list, max_messages: int = 50) -> list:
        if len(messages) <= max_messages:
            return messages

        keep_head = 3
        keep_tail = max_messages - keep_head
        snipped = len(messages) - keep_head - keep_tail
        return (
            messages[:keep_head]
            + [{"role": "user", "content": f"[snipped {snipped} messages]"}]
            + messages[-keep_tail:]
        )

    def collect_tool_results(self, messages: list) -> list:
        blocks = []
        for message_index, message in enumerate(messages):
            if message.get("role") != "user" or not isinstance(message.get("content"), list):
                continue
            for block_index, block in enumerate(message["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    blocks.append((message_index, block_index, block))
        return blocks

    def collect_tool_uses(self, messages: list) -> dict:
        tool_uses = {}
        for message in messages:
            if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                continue

            for block in message["content"]:
                if self.block_value(block, "type") != "tool_use":
                    continue

                tool_use_id = self.block_value(block, "id")
                if not tool_use_id:
                    continue

                tool_input = self.block_value(block, "input", {})
                if not isinstance(tool_input, dict):
                    tool_input = {}

                tool_uses[str(tool_use_id)] = {
                    "name": self.block_value(block, "name", "unknown"),
                    "input": tool_input,
                }
        return tool_uses

    def block_value(self, block, key: str, default=None):
        if isinstance(block, dict):
            return block.get(key, default)
        return getattr(block, key, default)

    def micro_compact(self, messages: list) -> list:
        tool_results = self.collect_tool_results(messages)
        if len(tool_results) <= self.keep_recent_tool_results:
            return messages

        tool_uses = self.collect_tool_uses(messages)
        for _, _, block in tool_results[:-self.keep_recent_tool_results]:
            content = str(block.get("content", ""))
            if len(content) <= 120 or self.is_compacted_placeholder(content):
                continue

            tool_use_id = str(block.get("tool_use_id", "unknown"))
            tool_info = tool_uses.get(tool_use_id, {})
            tool_name = tool_info.get("name", "unknown")
            tool_input = tool_info.get("input", {})
            saved_path = self.persist_compacted_tool_result(
                tool_use_id,
                tool_name,
                tool_input,
                content,
            )
            block["content"] = self.compacted_placeholder(
                tool_name,
                tool_input,
                saved_path,
                content,
            )
        return messages

    def is_compacted_placeholder(self, content: str) -> bool:
        markers = (
            "[Compacted tool result]",
            "[Compacted skill content]",
            "[Earlier tool result compacted.",
        )
        return content.startswith(markers)

    def persist_compacted_tool_result(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict,
        content: str,
    ) -> Path:
        self.compacted_results_dir.mkdir(parents=True, exist_ok=True)
        safe_id = self.safe_id(tool_use_id)
        path = self.compacted_results_dir / f"{safe_id or 'unknown'}.json"

        if not path.exists():
            payload = {
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "content": content,
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

        return path

    def compacted_placeholder(
        self,
        tool_name: str,
        tool_input: dict,
        saved_path: Path,
        content: str,
    ) -> str:
        relative_path = self.relative_display_path(saved_path)
        preview = content[:500]
        input_text = json.dumps(tool_input, ensure_ascii=False, default=str)

        if tool_name == "load_skill":
            skill_name = tool_input.get("name", "unknown")
            return (
                "[Compacted skill content]\n"
                f"Skill: {skill_name}\n"
                f"Full result saved at: {relative_path}\n"
                f"Call load_skill(name=\"{skill_name}\") again if you need the full skill instructions.\n"
                f"Preview:\n{preview}"
            )

        return (
            "[Compacted tool result]\n"
            f"Tool: {tool_name}\n"
            f"Input: {input_text}\n"
            f"Full result saved at: {relative_path}\n"
            f"Use read_file(path=\"{relative_path}\") if you need the full result again.\n"
            f"Preview:\n{preview}"
        )

    def relative_display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.workdir).as_posix()
        except ValueError:
            return str(path)

    def safe_id(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))

    def persist_large_output(self, tool_use_id: str, output: str) -> str:
        if len(output) <= self.persist_threshold:
            return output

        self.tool_results_dir.mkdir(parents=True, exist_ok=True)
        safe_id = self.safe_id(tool_use_id)
        path = self.tool_results_dir / f"{safe_id or 'unknown'}.txt"
        if not path.exists():
            path.write_text(output, encoding="utf-8", errors="replace")

        return (
            "<persisted-output>\n"
            f"Full output: {path}\n"
            f"Preview:\n{output[:2000]}\n"
            "</persisted-output>"
        )

    def tool_result_budget(self, messages: list, max_bytes: int = 200_000) -> list:
        last = messages[-1] if messages else None
        if not last or last.get("role") != "user" or not isinstance(last.get("content"), list):
            return messages

        blocks = [
            block
            for block in last["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        total = sum(len(str(block.get("content", ""))) for block in blocks)
        if total <= max_bytes:
            return messages

        ranked = sorted(blocks, key=lambda block: len(str(block.get("content", ""))), reverse=True)
        for block in ranked:
            if total <= max_bytes:
                break

            content = str(block.get("content", ""))
            if len(content) <= self.persist_threshold:
                continue

            block["content"] = self.persist_large_output(block.get("tool_use_id", "unknown"), content)
            total = sum(len(str(item.get("content", ""))) for item in blocks)

        return messages

    def write_transcript(self, messages: list) -> Path:
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with path.open("w", encoding="utf-8") as file:
            for message in messages:
                file.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        return path

    def summarize_history(self, messages: list) -> str:
        conversation = json.dumps(messages, ensure_ascii=False, default=str)[:80_000]
        prompt = (
            "Summarize this personal-assistant conversation so work can continue.\n"
            "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
            "4. remaining work, 5. user constraints.\n"
            "Be compact but concrete.\n\n"
            f"{conversation}"
        )
        response = self.client.messages.create(
            task_type="compact",
            model=self.model,
            system="You summarize agent conversation history for context compaction.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=2000,
        )
        return self.extract_text(response.content).strip() or "(empty summary)"

    def compact_history(self, messages: list) -> list:
        transcript_path = self.write_transcript(messages)
        print(f"[transcript saved: {transcript_path}]")
        summary = self.summarize_history(messages)
        return self.build_compacted_messages(
            label="Compacted",
            summary=summary,
            messages=messages,
            keep_recent=6,
        )

    def reactive_compact(self, messages: list, level: int = 1) -> list:
        transcript_path = self.write_transcript(messages)
        print(f"[reactive transcript saved: {transcript_path}]")
        if level <= 1:
            try:
                return self.reactive_compact_level1(messages)
            except Exception as exc:
                if not self.is_prompt_too_long(exc):
                    raise
                print("[reactive compact level 1 failed; falling back to level 2]")

        if level <= 2:
            try:
                return self.reactive_compact_level2(messages, transcript_path)
            except Exception as exc:
                print(f"[reactive compact level 2 failed; falling back to level 3: {exc}]")

        return self.reactive_compact_level3(messages, transcript_path)

    def reactive_compact_level1(self, messages: list) -> list:
        summary = self.summarize_history(messages)
        return self.build_compacted_messages(
            label="Reactive compact level 1",
            summary=summary,
            messages=messages,
            keep_recent=6,
        )

    def reactive_compact_level2(self, messages: list, transcript_path: Path) -> list:
        summary = self.summarize_history_in_chunks(messages, transcript_path)
        return self.build_compacted_messages(
            label="Reactive compact level 2",
            summary=summary,
            messages=messages,
            keep_recent=6,
        )

    def reactive_compact_level3(self, messages: list, transcript_path: Path) -> list:
        return self.last_resort_messages(messages, transcript_path)

    def summarize_history_in_chunks(
        self,
        messages: list,
        transcript_path: Path,
        initial_chunk_size: int = 30_000,
        min_chunk_size: int = 4_000,
    ) -> str:
        text = json.dumps(messages, ensure_ascii=False, default=str)
        chunk_size = initial_chunk_size
        last_error = None

        while chunk_size >= min_chunk_size:
            try:
                chunks = self.split_text(text, chunk_size)
                summaries = [
                    self.summarize_chunk(chunk, index + 1, len(chunks))
                    for index, chunk in enumerate(chunks)
                ]
                return self.combine_chunk_summaries(summaries, transcript_path)
            except Exception as exc:
                last_error = exc
                if not self.is_prompt_too_long(exc):
                    raise
                chunk_size = chunk_size // 2

        raise RuntimeError(f"Chunked summary failed after shrinking chunks: {last_error}")

    def split_text(self, text: str, chunk_size: int) -> list[str]:
        return [text[index:index + chunk_size] for index in range(0, len(text), chunk_size)]

    def summarize_chunk(self, chunk: str, index: int, total: int) -> str:
        prompt = (
            f"Summarize chunk {index}/{total} of a personal-assistant conversation.\n"
            "Preserve concrete facts: current goal, decisions, files, errors, tool results, "
            "user constraints, and remaining work.\n"
            "Be compact but do not omit important technical details.\n\n"
            f"{chunk}"
        )
        response = self.client.messages.create(
            task_type="compact_chunk",
            model=self.model,
            system="You summarize one chunk of a long personal-assistant transcript.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=1200,
        )
        text = self.extract_text(response.content).strip()
        return f"## Chunk {index}/{total}\n{text or '(empty chunk summary)'}"

    def combine_chunk_summaries(self, summaries: list[str], transcript_path: Path) -> str:
        combined = "\n\n".join(summaries)
        transcript_note = f"Full transcript saved at: {self.relative_display_path(transcript_path)}"

        if len(combined) <= 30_000:
            return f"{transcript_note}\n\n{combined}"

        prompt = (
            "Combine these chunk summaries into one compact recovery summary.\n"
            "Preserve current goal, decisions, files, errors, user constraints, and remaining work.\n\n"
            f"{combined[:60_000]}"
        )
        response = self.client.messages.create(
            task_type="compact_merge",
            model=self.model,
            system="You merge chunk summaries into a compact recovery summary.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=2000,
        )
        final_summary = self.extract_text(response.content).strip() or combined[:30_000]
        return f"{transcript_note}\n\n{final_summary}"

    def last_resort_messages(self, messages: list, transcript_path: Path) -> list:
        first = self.first_preserved_message(messages)
        recent = self.recent_messages(messages, 2, skip_first=True)
        transcript_note = (
            "[Last resort compact]\n"
            f"Full transcript saved at: {self.relative_display_path(transcript_path)}\n"
            "Only the first message and the latest two messages were preserved to keep the agent running.\n"
            "TODO: when background tasks are added, start an async chunk-summary job here and save the result for later read_file access."
        )

        if first is None:
            return [{"role": "user", "content": transcript_note}, *recent]

        first = self.add_note_to_message(first, transcript_note)
        return [first, *recent]

    def add_note_to_message(self, message: dict, note: str) -> dict:
        safe = copy.deepcopy(message)
        content = safe.get("content")
        if isinstance(content, str):
            safe["content"] = f"{note}\n\nOriginal first message:\n{content}"
            return safe

        safe["content"] = f"{note}\n\nOriginal first message content was not plain text."
        return safe

    def build_compacted_messages(
        self,
        label: str,
        summary: str,
        messages: list,
        keep_recent: int,
    ) -> list:
        compacted = [{"role": "user", "content": f"[{label}]\n\n{summary}"}]

        first_message = self.first_preserved_message(messages)
        if first_message is not None:
            compacted.append(first_message)

        compacted.extend(self.recent_messages(messages, keep_recent, skip_first=True))
        return compacted

    def first_preserved_message(self, messages: list) -> dict | None:
        if not messages:
            return None
        return self.safe_message_copy(
            messages[0],
            max_chars=4000,
            notice="[First message preserved but truncated]",
        )

    def recent_messages(self, messages: list, count: int, skip_first: bool = False) -> list:
        if count <= 0:
            return []

        indexed_messages = [
            (index, message)
            for index, message in enumerate(messages)
            if not (skip_first and index == 0)
        ]
        selected = indexed_messages[-count:]
        if not selected:
            return []

        start = selected[0][0]
        recent = [self.safe_message_copy(message, max_chars=8000) for _, message in selected]

        # If the preserved tail starts with tool_result, include the matching
        # assistant tool_use before it so the message sequence remains valid.
        if start > 1 and recent and self.message_has_tool_result(recent[0]):
            previous = self.safe_message_copy(messages[start - 1], max_chars=8000)
            if previous.get("role") == "assistant":
                recent.insert(0, previous)

        return recent

    def safe_summary_input(self, messages: list) -> list:
        safe_messages = []
        first = self.first_preserved_message(messages)
        if first is not None:
            safe_messages.append(first)
        safe_messages.extend(self.recent_messages(messages, 2, skip_first=True))
        return safe_messages

    def safe_message_copy(
        self,
        message: dict,
        max_chars: int,
        notice: str = "[Message content truncated]",
    ) -> dict:
        safe = copy.deepcopy(message)
        content = safe.get("content")
        safe["content"] = self.safe_content_copy(content, max_chars, notice)
        return safe

    def safe_content_copy(self, content, max_chars: int, notice: str):
        if isinstance(content, str):
            if len(content) <= max_chars:
                return content
            return f"{notice}\nOriginal length: {len(content)} chars\nPreview:\n{content[:max_chars]}"

        if isinstance(content, list):
            copied = []
            for block in content:
                if not isinstance(block, dict):
                    copied.append(block)
                    continue

                item = copy.deepcopy(block)
                if "content" in item and isinstance(item["content"], str):
                    item["content"] = self.safe_content_copy(item["content"], max_chars, notice)
                copied.append(item)
            return copied

        text = str(content)
        if len(text) <= max_chars:
            return text
        return f"{notice}\nOriginal length: {len(text)} chars\nPreview:\n{text[:max_chars]}"

    def message_has_tool_result(self, message: dict) -> bool:
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)

    def is_prompt_too_long(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "prompt_too_long" in text or "too many tokens" in text or "context" in text

    def extract_text(self, content) -> str:
        if not isinstance(content, list):
            return str(content)
        return "\n".join(
            str(getattr(block, "text", ""))
            for block in content
            if getattr(block, "type", None) == "text"
        )
