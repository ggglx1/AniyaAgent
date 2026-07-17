#!/usr/bin/env python3

import sys

if sys.path and sys.path[0].replace("/", "\\").lower().endswith("\\channel"):
    sys.path.pop(0)

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from main.channel.run_scheduler import main  # noqa: E402


if __name__ == "__main__":
    # Compatibility command: WeChat delivery is owned by the scheduler process.
    main()
