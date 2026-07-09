import base64
import json
import os
import queue
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .base import AgentResponse, ChannelMessage, ChannelSendResult
from .runtime import ChannelRuntime
from .types import ChannelKind, TrustLevel


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.0.0"
CLIENT_VERSION = "131072"
BOT_TYPE = "3"
DEFAULT_LONG_POLL_TIMEOUT = 35
DEFAULT_API_TIMEOUT = 15
QR_POLL_TIMEOUT = 35
QR_LOGIN_TIMEOUT_SECONDS = 480
QR_MAX_REFRESHES = 10
SESSION_EXPIRED_CODE = -14
TEXT_CHUNK_LIMIT = 4000

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5


def default_credentials_path() -> Path:
    configured = os.environ.get("ANIYAAGENT_WEIXIN_CREDENTIALS", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".aniyaagent" / "weixin_credentials.json"


def load_credentials(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[Weixin] Failed to load credentials: {exc}")
    return {}


def save_credentials(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except Exception:
        pass
    os.replace(tmp_path, path)


def random_wechat_uin() -> str:
    raw = str(random.randint(0, 0xFFFFFFFF)).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def build_headers(token: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": random_wechat_uin(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class WeixinApi:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str = ""):
        self.base_url = base_url
        self.token = token

    def post(self, endpoint: str, body: dict, timeout: int = DEFAULT_API_TIMEOUT) -> dict:
        body.setdefault("base_info", {}).setdefault("channel_version", CHANNEL_VERSION)
        response = requests.post(
            ensure_trailing_slash(self.base_url) + endpoint,
            json=body,
            headers=build_headers(self.token),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_updates(self, get_updates_buf: str = "", timeout: int = DEFAULT_LONG_POLL_TIMEOUT) -> dict:
        return self.post(
            "ilink/bot/getupdates",
            {"get_updates_buf": get_updates_buf},
            timeout=timeout + 5,
        )

    def send_text(self, to_user_id: str, text: str, context_token: str) -> dict:
        return self.post(
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": uuid.uuid4().hex[:16],
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
                    "context_token": context_token,
                }
            },
        )

    def fetch_qr_code(self) -> dict:
        url = ensure_trailing_slash(self.base_url) + f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()

    def poll_qr_status(self, qrcode: str, timeout: int = QR_POLL_TIMEOUT) -> dict:
        url = ensure_trailing_slash(self.base_url) + f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}"
        headers = {
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": CLIENT_VERSION,
        }
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"status": "wait"}


class WeixinChannel:
    def __init__(
        self,
        channel_runtime: ChannelRuntime,
        channel_id: str = "weixin",
        base_url: str = DEFAULT_BASE_URL,
        token: str = "",
        credentials_path: Path | None = None,
        auto_login: bool = True,
    ):
        self.channel_runtime = channel_runtime
        self.channel_id = channel_id
        self.kind = ChannelKind.WEIXIN
        self.trust_level = TrustLevel.MEDIUM
        self.base_url = base_url
        self.token = token
        self.credentials_path = credentials_path or default_credentials_path()
        self.auto_login = auto_login
        self.api: WeixinApi | None = None
        self.login_status = "idle"
        self.current_qr_url = ""
        self.get_updates_buf = ""
        self.context_tokens: dict[str, str] = {}
        self.received_messages: dict[str, float] = {}
        self.outbox: "queue.Queue[AgentResponse]" = queue.Queue()
        self.stop_event = threading.Event()
        self.poll_thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def start(self) -> bool:
        self.stop_event.clear()
        creds = load_credentials(self.credentials_path)
        if not self.token:
            self.token = str(os.environ.get("ANIYAAGENT_WEIXIN_TOKEN") or creds.get("token") or "")
        if creds.get("base_url"):
            self.base_url = str(creds["base_url"])
        self.context_tokens.update(
            {
                str(user_id): str(token)
                for user_id, token in (creds.get("context_tokens") or {}).items()
                if user_id and token
            }
        )

        if not self.token and self.auto_login:
            login = self.login_with_qr()
            if not login:
                return False
            self.token = login["token"]
            self.base_url = login.get("base_url", self.base_url)

        if not self.token:
            print("[Weixin] Missing bot token. Run with auto_login=True or set ANIYAAGENT_WEIXIN_TOKEN.")
            return False

        self.api = WeixinApi(base_url=self.base_url, token=self.token)
        self.login_status = "logged_in"
        if self.poll_thread is None or not self.poll_thread.is_alive():
            self.poll_thread = threading.Thread(target=self.poll_loop, daemon=True, name="aniyaagent-weixin")
            self.poll_thread.start()
        print(f"[Weixin] Channel started. Credentials: {self.credentials_path}")
        return True

    def serve_forever(self) -> None:
        if not self.start():
            return
        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()

    def send(self, response: AgentResponse) -> ChannelSendResult:
        receiver = response.conversation_id
        context_token = self.context_tokens.get(receiver, "")
        if not self.api:
            return ChannelSendResult(False, "Weixin API is not initialized.")
        if not context_token:
            return ChannelSendResult(False, f"No context_token for receiver: {receiver}")

        text = response.text if response.text else response.error
        if not text:
            return ChannelSendResult(True, "empty response")

        try:
            for chunk in split_text(text, TEXT_CHUNK_LIMIT):
                result = self.api.send_text(receiver, chunk, context_token)
                self.check_send_response(receiver, result)
                time.sleep(0.2)
            return ChannelSendResult(True, "sent")
        except Exception as exc:
            return ChannelSendResult(False, f"send failed: {type(exc).__name__}: {exc}")

    def login_with_qr(self) -> dict:
        api = WeixinApi(base_url=self.base_url)
        try:
            qr_response = api.fetch_qr_code()
        except Exception as exc:
            print(f"[Weixin] Failed to fetch QR code: {exc}")
            return {}

        qrcode = str(qr_response.get("qrcode") or "")
        qrcode_url = str(qr_response.get("qrcode_img_content") or "")
        if not qrcode:
            print(f"[Weixin] QR response has no qrcode field: {qr_response}")
            return {}

        self.current_qr_url = qrcode_url
        self.login_status = "waiting_scan"
        print_qr(qrcode_url)
        print("[Weixin] Waiting for WeChat scan confirmation...")

        scanned_printed = False
        refresh_count = 0
        deadline = time.time() + QR_LOGIN_TIMEOUT_SECONDS

        while not self.stop_event.is_set():
            if time.time() >= deadline:
                print(f"[Weixin] QR login timed out after {QR_LOGIN_TIMEOUT_SECONDS}s.")
                return {}

            try:
                status_response = api.poll_qr_status(qrcode)
            except Exception as exc:
                print(f"[Weixin] QR status poll failed: {exc}")
                return {}

            status = str(status_response.get("status") or "wait")
            if status == "scaned":
                self.login_status = "scanned"
                if not scanned_printed:
                    print("[Weixin] Scanned. Confirm login on your phone.")
                    scanned_printed = True
            elif status == "expired":
                refresh_count += 1
                if refresh_count >= QR_MAX_REFRESHES:
                    print("[Weixin] QR code expired too many times.")
                    return {}
                try:
                    qr_response = api.fetch_qr_code()
                    qrcode = str(qr_response.get("qrcode") or "")
                    qrcode_url = str(qr_response.get("qrcode_img_content") or "")
                    self.current_qr_url = qrcode_url
                    scanned_printed = False
                    print_qr(qrcode_url)
                except Exception as exc:
                    print(f"[Weixin] QR refresh failed: {exc}")
                    return {}
            elif status == "confirmed":
                bot_token = str(status_response.get("bot_token") or "")
                bot_id = str(status_response.get("ilink_bot_id") or "")
                user_id = str(status_response.get("ilink_user_id") or "")
                result_base_url = str(status_response.get("baseurl") or self.base_url)
                if not bot_token or not bot_id:
                    print(f"[Weixin] Login confirmed but token/bot_id is missing: {status_response}")
                    return {}

                creds = {
                    "token": bot_token,
                    "base_url": result_base_url,
                    "bot_id": bot_id,
                    "user_id": user_id,
                    "context_tokens": self.context_tokens,
                }
                save_credentials(self.credentials_path, creds)
                self.current_qr_url = ""
                self.login_status = "logged_in"
                print(f"[Weixin] Login succeeded. bot_id={bot_id}")
                return {"token": bot_token, "base_url": result_base_url, "bot_id": bot_id}

            self.stop_event.wait(1)

        return {}

    def poll_loop(self) -> None:
        consecutive_failures = 0
        while not self.stop_event.is_set():
            if self.api is None:
                self.stop_event.wait(1)
                continue
            try:
                response = self.api.get_updates(self.get_updates_buf)
                ret = response.get("ret", 0)
                errcode = response.get("errcode", 0)
                if ret != 0 or errcode != 0:
                    if ret == SESSION_EXPIRED_CODE or errcode == SESSION_EXPIRED_CODE:
                        print("[Weixin] Session expired. Delete credentials and scan again.")
                        self.stop_event.wait(30)
                        continue
                    consecutive_failures += 1
                    print(f"[Weixin] getUpdates error ret={ret} errcode={errcode}: {response.get('errmsg', '')}")
                    self.stop_event.wait(30 if consecutive_failures >= 3 else 2)
                    if consecutive_failures >= 3:
                        consecutive_failures = 0
                    continue

                consecutive_failures = 0
                new_buf = response.get("get_updates_buf", "")
                if new_buf:
                    self.get_updates_buf = str(new_buf)
                for raw_message in response.get("msgs", []) or []:
                    self.process_message(raw_message)
            except Exception as exc:
                if not self.stop_event.is_set():
                    consecutive_failures += 1
                    print(f"[Weixin] getUpdates exception: {type(exc).__name__}: {exc}")
                    self.stop_event.wait(30 if consecutive_failures >= 3 else 2)
                    if consecutive_failures >= 3:
                        consecutive_failures = 0

    def process_message(self, raw_message: dict[str, Any]) -> AgentResponse | None:
        if raw_message.get("message_type", 0) != 1:
            return None

        message_id = str(raw_message.get("message_id") or raw_message.get("seq") or uuid.uuid4().hex)
        if self.is_duplicate(message_id):
            return None

        from_user = str(raw_message.get("from_user_id") or "")
        context_token = str(raw_message.get("context_token") or "")
        if context_token and from_user:
            self.update_context_token(from_user, context_token)

        text, files, images = parse_message_items(raw_message)
        if not text and (files or images):
            text = "[收到微信媒体消息，当前 AniyaAgent WeixinChannel 文本版暂未下载媒体。]"
        if not text:
            return None

        response = self.channel_runtime.handle_message(
            ChannelMessage(
                channel_id=self.channel_id,
                user_id=from_user,
                conversation_id=from_user,
                text=text,
                kind=ChannelKind.WEIXIN,
                trust_level=TrustLevel.MEDIUM,
                files=files,
                images=images,
                metadata={
                    "message_id": message_id,
                    "context_token": context_token,
                    "raw_message_type": raw_message.get("message_type"),
                },
            ),
            deliver=False,
        )
        self.send(response)
        return response

    def is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        with self.lock:
            for old_id, seen_at in list(self.received_messages.items()):
                if now - seen_at > 7 * 24 * 60 * 60:
                    self.received_messages.pop(old_id, None)
            if message_id in self.received_messages:
                return True
            self.received_messages[message_id] = now
            return False

    def update_context_token(self, user_id: str, context_token: str) -> None:
        with self.lock:
            if self.context_tokens.get(user_id) == context_token:
                return
            self.context_tokens[user_id] = context_token
            creds = load_credentials(self.credentials_path)
            creds["token"] = self.token or creds.get("token", "")
            creds["base_url"] = self.base_url
            creds["context_tokens"] = dict(self.context_tokens)
            save_credentials(self.credentials_path, creds)

    def check_send_response(self, receiver: str, response: dict) -> None:
        if response.get("ret") == SESSION_EXPIRED_CODE or response.get("errcode") == SESSION_EXPIRED_CODE:
            with self.lock:
                self.context_tokens.pop(receiver, None)
                creds = load_credentials(self.credentials_path)
                creds["context_tokens"] = dict(self.context_tokens)
                save_credentials(self.credentials_path, creds)


def parse_message_items(raw_message: dict[str, Any]) -> tuple[str, list[dict], list[dict]]:
    text_parts: list[str] = []
    files: list[dict] = []
    images: list[dict] = []

    for item in raw_message.get("item_list", []) or []:
        item_type = item.get("type", 0)
        if item_type == ITEM_TEXT:
            text = str((item.get("text_item") or {}).get("text") or "")
            if text:
                text_parts.append(text)
            ref_text = parse_ref_text(item.get("ref_msg"))
            if ref_text:
                text_parts.insert(0, ref_text)
        elif item_type == ITEM_VOICE:
            voice_text = str((item.get("voice_item") or {}).get("text") or "")
            if voice_text:
                text_parts.append(voice_text)
            else:
                files.append({"type": "voice", "metadata": item})
        elif item_type == ITEM_IMAGE:
            images.append({"type": "image", "metadata": item})
        elif item_type == ITEM_FILE:
            file_item = item.get("file_item") or {}
            files.append({"type": "file", "name": file_item.get("file_name", ""), "metadata": item})
        elif item_type == ITEM_VIDEO:
            files.append({"type": "video", "metadata": item})

    return "\n".join(part for part in text_parts if part).strip(), files, images


def parse_ref_text(ref_msg) -> str:
    if not isinstance(ref_msg, dict):
        return ""
    title = str(ref_msg.get("title") or "")
    message_item = ref_msg.get("message_item") or {}
    body = ""
    if message_item.get("type") == ITEM_TEXT:
        body = str((message_item.get("text_item") or {}).get("text") or "")
    parts = [part for part in [title, body] if part]
    return f"[引用: {' | '.join(parts)}]" if parts else ""


def split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def print_qr(qrcode_url: str) -> None:
    print("\n" + "=" * 60)
    print("  请使用微信扫描二维码登录")
    print(f"  二维码链接: {qrcode_url}")
    print("=" * 60)
    try:
        import io
        import qrcode as qr_lib

        qr = qr_lib.QRCode(error_correction=qr_lib.constants.ERROR_CORRECT_L, box_size=1, border=1)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        buffer = io.StringIO()
        qr.print_ascii(out=buffer, invert=True)
        print(buffer.getvalue())
    except Exception:
        print(f"二维码链接: {qrcode_url}")
        print("安装 qrcode 后可在终端直接显示二维码: pip install qrcode")
