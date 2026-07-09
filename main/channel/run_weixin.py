#!/usr/bin/env python3

import sys

if sys.path and sys.path[0].replace("/", "\\").lower().endswith("\\channel"):
    sys.path.pop(0)

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from main.agent import main_loop as MainLoop  # noqa: E402
from main.channel.weixin import DEFAULT_BASE_URL, WeixinChannel, default_credentials_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AniyaAgent Weixin iLink channel.")
    parser.add_argument("--base-url", default=os.environ.get("ANIYAAGENT_WEIXIN_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("ANIYAAGENT_WEIXIN_TOKEN", ""))
    parser.add_argument("--credentials", type=Path, default=default_credentials_path())
    parser.add_argument("--no-auto-login", action="store_true")
    args = parser.parse_args()

    channel = WeixinChannel(
        MainLoop.get_channel_runtime(),
        base_url=args.base_url,
        token=args.token,
        credentials_path=args.credentials,
        auto_login=not args.no_auto_login,
    )
    MainLoop.channel_registry.register(channel)
    channel.serve_forever()


if __name__ == "__main__":
    main()
