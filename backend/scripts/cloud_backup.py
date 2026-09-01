"""CLI used by ECS cron jobs for verified local cloud backups."""
from __future__ import annotations

import argparse
import json

from app import config
from app.services.backup import (
    create_full_backup,
    maybe_daily_backup,
    verify_full_backup,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="交接班系统云端备份工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("daily", help="当天没有每日备份时创建一份")
    subparsers.add_parser("create", help="立即创建一份完整备份")
    verify_parser = subparsers.add_parser("verify", help="校验指定完整备份")
    verify_parser.add_argument("backup_id")
    args = parser.parse_args()

    config.validate_runtime_configuration()
    if args.command == "daily":
        result = maybe_daily_backup()
        payload = result or {"status": "already_created_today"}
    elif args.command == "create":
        payload = create_full_backup(reason="scheduled-cloud", replicate=False)
    else:
        payload = verify_full_backup(args.backup_id)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
