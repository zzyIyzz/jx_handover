"""Show, and when needed repair, where the live data actually is.

Deployment problems in this project have repeatedly come down to one question:
which directory holds the real database?  The answer used to change with the run
mode, so an upgrade could leave the service pointing at an empty folder while the
operator's accounts and handover records sat untouched a few centimetres away.

This script answers the question out loud and, with ``--adopt``, copies an old
directory into the active one.  Copying is deliberate: nothing is ever moved,
renamed or deleted, so a wrong guess costs disk space and never costs data.

Examples
--------
Where is my data, and is anything left behind elsewhere?::

    python backend/scripts/data_location.py

Rescue a specific old directory after confirming it is the right one::

    python backend/scripts/data_location.py --adopt /www/wwwroot/jx_handover/runtime-server
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import data_root as data_root_service  # noqa: E402


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _print_report(report: dict) -> None:
    print("当前生效的数据目录")
    print(f"  数据根目录 : {report['data_root']}")
    print(f"  数据库文件 : {report['database_path']}")
    if report["has_database"]:
        print(f"  数据库大小 : {_human_size(int(report['database_size']))}")
    else:
        print("  数据库大小 : （尚未建立，首次启动会创建空库）")
    print(
        "  目录来源   : "
        + (
            "环境变量 JX_HANDOVER_DATA_DIR 显式指定"
            if report["data_root_explicit"]
            else "程序默认（未显式配置 JX_HANDOVER_DATA_DIR）"
        )
    )
    if report.get("adopted_from"):
        print(f"  曾接管自   : {report['adopted_from']}")

    legacy = report["legacy_roots_with_data"]
    print()
    if legacy:
        print("另有历史目录仍保存着数据库（当前服务没有使用它们）：")
        for path in legacy:
            root = Path(path)
            size = 0
            try:
                size = data_root_service.database_path(root).stat().st_size
            except OSError:
                pass
            print(f"  - {path}（{_human_size(size)}）")
        print("确认哪一个是真实数据后，可执行：")
        print(f"  python backend/scripts/data_location.py --adopt {legacy[0]}")
    else:
        print("没有发现其他保存着数据库的历史目录。")

    warnings = report.get("warnings") or []
    if warnings:
        print()
        print("提示：")
        for item in warnings:
            print(f"  ! {item}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="查看当前数据目录，或把历史数据目录安全接管为当前数据目录。"
    )
    parser.add_argument(
        "--adopt",
        metavar="目录",
        nargs="?",
        const="__auto__",
        help="从指定历史目录复制数据到当前数据目录；不带参数时自动选择唯一候选",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="配合 --adopt 使用，只显示将要做什么"
    )
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    if args.adopt is None:
        report = data_root_service.data_root_report()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        return

    if args.dry_run:
        source = None if args.adopt == "__auto__" else Path(args.adopt)
        # Same decision function the real run uses, so a dry run cannot promise
        # something the service would then refuse to do.
        plan = data_root_service.adoption_plan(source=source)
        print(f"当前数据目录 : {plan['active_root']}")
        print(f"已有数据库   : {'是' if plan['active_has_database'] else '否'}")
        print(f"候选历史目录 : {plan['candidates'] or '（无）'}")
        print(f"判定结果     : {plan['state']}")
        print(f"说明         : {plan['message']}")
        print("试运行不会复制任何文件。")
        if plan["state"] in {"failed", "ambiguous"}:
            raise SystemExit(2)
        return

    source = None if args.adopt == "__auto__" else Path(args.adopt)
    result = data_root_service.adopt_legacy_data_root(source=source)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"结果：{result['state']}")
        print(result["message"])
        if result["state"] == "adopted":
            print(
                f"已复制 {result['copied_files']} 个文件"
                f"（{_human_size(int(result['copied_bytes']))}），"
                f"完整性检查：{result['database_check']}。"
            )
            print("原目录未被修改或删除，确认无误后可自行清理。")
            print("请重启服务，让后端使用接管后的数据目录。")
    if result["state"] in {"failed", "ambiguous"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
