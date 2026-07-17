#!/usr/bin/env python3
"""Dedicated owner for reminders, routines, cron delivery, and daily maintenance."""
import sys
import argparse
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from main.agent import main_loop as MainLoop  # noqa: E402
from main.conversation import ConversationRetentionService  # noqa: E402
from main.channel.weixin import DEFAULT_BASE_URL, WeixinChannel, default_credentials_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AniyaAgent's single scheduler and WeChat notification sender.")
    parser.add_argument("--base-url", default=os.environ.get("ANIYAAGENT_WEIXIN_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("ANIYAAGENT_WEIXIN_TOKEN", ""))
    parser.add_argument("--credentials", type=Path, default=default_credentials_path())
    parser.add_argument("--no-auto-login", action="store_true")
    args = parser.parse_args()

    weixin = WeixinChannel(
        MainLoop.get_channel_runtime(), base_url=args.base_url, token=args.token,
        credentials_path=args.credentials, auto_login=not args.no_auto_login,
    )
    MainLoop.channel_registry.register(weixin)
    if not weixin.start():
        raise RuntimeError("WeChat sender could not start; scheduler refuses to run without the durable notification channel.")
    MainLoop.start_background_services()
    retention = ConversationRetentionService(MainLoop.conversation_memory.repository, MainLoop.personal_memory_manager)
    last_cleanup_date = ""
    print("AniyaAgent scheduler is running.")
    try:
        while True:
            today = datetime.now().date().isoformat()
            if today != last_cleanup_date:
                retention.cleanup_expired_operational_artifacts()
                last_cleanup_date = today
            MainLoop.time.sleep(60)
    except KeyboardInterrupt:
        MainLoop.reminder_dispatcher.stop()
        MainLoop.routine_dispatcher.stop()
        MainLoop.memory_maintenance.stop()
        weixin.stop()


if __name__ == "__main__":
    main()
