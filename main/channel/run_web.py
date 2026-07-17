#!/usr/bin/env python3

import sys

if sys.path and sys.path[0].replace("/", "\\").lower().endswith("\\channel"):
    sys.path.pop(0)

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from main.application import create_application  # noqa: E402
from main.channel.web import WebChannel  # noqa: E402
from main.conversation import MemoryAdminService  # noqa: E402


def main() -> None:
    app = create_application()
    parser = argparse.ArgumentParser(description="Run AniyaAgent WebChannel HTTP/SSE server.")
    parser.add_argument("--host", default=os.environ.get("ANIYAAGENT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ANIYAAGENT_WEB_PORT", "9528")))
    parser.add_argument("--token", default=os.environ.get("ANIYAAGENT_WEB_TOKEN", ""))
    args = parser.parse_args()

    channel = WebChannel(
        app.web_runtime(),
        host=args.host,
        port=args.port,
        auth_token=args.token,
        llm_control=app.runtime.client,
        memory_admin=MemoryAdminService(*app.memory_admin_dependencies),
        application=app,
    )
    app.runtime.channel_registry.register(channel)
    app.runtime.permissions.ask_user = channel.ask_user

    print(f"AniyaAgent WebChannel listening on http://{args.host}:{args.port}")
    print("POST /message then GET /stream?request_id=... for SSE updates.")
    if args.token:
        print("Auth: use Authorization: Bearer <token> or x-aniyaagent-token.")
    channel.serve_forever()


if __name__ == "__main__":
    main()
