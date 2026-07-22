from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class AttachmentService:
    """Private attachment storage with bounded text extraction for model context."""
    allowed = {"text/plain", "text/markdown", "text/csv", "application/json", "application/pdf", "image/png", "image/jpeg", "image/webp"}
    max_bytes = 20 * 1024 * 1024

    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve(); self.root = self.workdir / ".attachments"; self.root.mkdir(parents=True, exist_ok=True); self.db = self.root / "attachments.db"; self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.db); connection.row_factory = sqlite3.Row; return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS attachments (attachment_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, original_name TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, storage_path TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL DEFAULT '', extracted_text_path TEXT NOT NULL DEFAULT '', preview_path TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}')""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_attachments_owner_created ON attachments(owner_id, created_at)")

    def upload(self, filename: str, data: bytes, media_type: str = "", owner_id: str = "local") -> dict:
        if not data: raise ValueError("Attachment is empty")
        if len(data) > self.max_bytes: raise ValueError("Attachment exceeds 20 MiB limit")
        detected = self.detect_type(filename, data, media_type)
        if detected not in self.allowed: raise ValueError(f"Unsupported attachment type: {detected}")
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"; digest = hashlib.sha256(data).hexdigest(); safe_name = self.safe_name(filename); path = self.root / "files" / f"{attachment_id}_{safe_name}"; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        now = self.now(); metadata = {"filename": filename, "sha256": digest}; extracted = ""; status = "ready"
        try: extracted = self.extract(detected, data)
        except Exception as exc: status = "failed"; metadata["parse_error"] = f"{type(exc).__name__}: {exc}"
        extracted_path = ""
        if extracted:
            text_path = self.root / "extracted" / f"{attachment_id}.txt"; text_path.parent.mkdir(parents=True, exist_ok=True); text_path.write_text(extracted, encoding="utf-8"); extracted_path = str(text_path)
        record = {"attachment_id":attachment_id,"owner_id":owner_id,"original_name":filename[:255],"media_type":detected,"size_bytes":len(data),"sha256":digest,"storage_path":str(path),"status":status,"created_at":now,"expires_at":"","extracted_text_path":extracted_path,"preview_path":"","metadata":metadata}
        with self.connect() as connection:
            connection.execute("INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*[record[key] for key in ("attachment_id","owner_id","original_name","media_type","size_bytes","sha256","storage_path","status","created_at","expires_at","extracted_text_path","preview_path")], json.dumps(metadata, ensure_ascii=False)))
        return record

    def get(self, attachment_id: str, owner_id: str = "local") -> dict:
        with self.connect() as connection: row = connection.execute("SELECT * FROM attachments WHERE attachment_id=? AND owner_id=? AND status<>'deleted'", (attachment_id, owner_id)).fetchone()
        if not row: raise FileNotFoundError(f"Attachment not found: {attachment_id}")
        item = dict(row); item["metadata"] = json.loads(item.pop("metadata_json") or "{}"); return item

    def delete(self, attachment_id: str, owner_id: str = "local") -> None:
        item = self.get(attachment_id, owner_id)
        for key in ("storage_path", "extracted_text_path", "preview_path"):
            if item.get(key):
                try: Path(item[key]).unlink(missing_ok=True)
                except OSError: pass
        with self.connect() as connection: connection.execute("UPDATE attachments SET status='deleted' WHERE attachment_id=? AND owner_id=?", (attachment_id, owner_id))

    def context(self, attachment_ids: list[str], owner_id: str = "local", max_chars: int = 12_000) -> tuple[str, list[dict]]:
        chunks=[]; images=[]; remaining=max_chars
        for attachment_id in attachment_ids[:10]:
            item=self.get(attachment_id, owner_id)
            if item["media_type"].startswith("image/"):
                images.append({"attachment_id":attachment_id,"media_type":item["media_type"],"path":item["storage_path"]}); continue
            if item["status"] != "ready" or not item["extracted_text_path"]: chunks.append(f"[Attachment {item['original_name']} could not be parsed: {item['status']}]"); continue
            text=Path(item["extracted_text_path"]).read_text(encoding="utf-8", errors="replace")[:remaining]
            chunks.append(f"<attachment id=\"{attachment_id}\" name=\"{item['original_name']}\">\n{text}\n</attachment>"); remaining -= len(text)
            if remaining <= 0: break
        return "\n\n".join(chunks), images

    def extract(self, media_type: str, data: bytes) -> str:
        if media_type.startswith("image/"): return ""
        if media_type == "application/pdf": raise ValueError("PDF parser is not installed; attachment remains available for download")
        return data.decode("utf-8", errors="replace")[:2_000_000]

    def detect_type(self, filename: str, data: bytes, declared: str) -> str:
        if data.startswith(b"\x89PNG"): return "image/png"
        if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
        if data.startswith(b"%PDF-"): return "application/pdf"
        return declared.split(";", 1)[0].lower() or mimetypes.guess_type(filename)[0] or "text/plain"

    def safe_name(self, value: str) -> str: return "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in value)[:120] or "upload"
    def now(self) -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
