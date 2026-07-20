#!/usr/bin/env python3
"""手动生成当日复盘，可选推送。用法: python scripts/generate_today.py [--push] [--force]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.report import generate_report  # noqa: E402
from app.wechat import WeChatError, push_daily_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 A 股日复盘")
    parser.add_argument("--push", action="store_true", help="生成后微信群发")
    parser.add_argument("--force", action="store_true", default=True, help="强制重新拉取")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    report = generate_report(force=args.force)
    print(f"已生成 {report.get('date')}，点评: {report.get('commentary')}")
    if args.push:
        try:
            result = push_daily_report(report)
            print("推送结果:", result)
        except WeChatError as exc:
            print("推送失败:", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
