"""Offline recovery for the administrator right.

The management page, backups and restore are all guarded by ``require_admin``.
When the last administrator disappears - after a redeploy that dropped
``JX_ADMIN_NAMES``, or a database that was copied in from another machine - the
web UI offers no way back in, because the only screen that could grant the right
is itself admin-only.  This script is that way back in: it is meant to be run on
the server's own console, where filesystem access already implies control.

Examples
--------
List who can currently manage the system::

    python backend/scripts/manage_admin.py --list

Restore access for one person::

    python backend/scripts/manage_admin.py --grant 张三

Take the right back (never removes the last administrator)::

    python backend/scripts/manage_admin.py --revoke 张三
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.migrations import initialize_database  # noqa: E402
from app.models import AuditEvent, Staff  # noqa: E402
from app.security import count_administrators, is_administrator  # noqa: E402


def _database_is_present() -> bool:
    """True only when a real database file is already there to operate on."""
    candidate = Path(config.DATABASE_PATH)
    try:
        return candidate.is_file() and candidate.stat().st_size > 0
    except OSError:
        return False


def _resolve_staff(db, name: str) -> Staff:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("人员姓名不能为空。")
    rows = (
        db.query(Staff)
        .filter(Staff.is_active == 1, Staff.name == clean_name)
        .order_by(Staff.id)
        .limit(2)
        .all()
    )
    if not rows:
        raise ValueError(f"没有找到启用人员：{clean_name}")
    if len(rows) != 1:
        raise ValueError(f"存在多个同名启用人员，拒绝操作：{clean_name}")
    return rows[0]


def _audit(db, staff: Staff, action: str) -> None:
    db.add(AuditEvent(
        actor_name="服务器管理员（本机命令行）",
        actor_role="admin",
        method="POST",
        request_path=f"/local-admin/accounts/{staff.id}/{action}",
        response_status=200,
        client_ip="127.0.0.1",
        user_agent="manage_admin.py",
        request_id=uuid.uuid4().hex,
    ))


def list_administrators() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(Staff).filter(Staff.is_active == 1).order_by(Staff.name).all()
        administrators = []
        for staff in rows:
            if not is_administrator(staff):
                continue
            administrators.append({
                "name": staff.name,
                "staff_id": staff.id,
                # An env-pinned name cannot be revoked here; saying so up front
                # saves a round trip that would only fail anyway.
                "from_env": config.is_admin_name(staff.name),
                "stored": bool(getattr(staff, "is_admin", 0)),
            })
        return {
            "status": "listed",
            "data_root": str(config.USER_DATA_ROOT),
            "database_path": str(config.DATABASE_PATH),
            "administrators": administrators,
            "env_admin_names": sorted(config.ADMIN_NAMES),
            # Reported so "(none)" is not read as "nobody will ever be able to
            # log in": on the next service start the recovery name is promoted
            # and the management page becomes reachable again.
            "recovery_admin_names": sorted(
                name for name in config.DEFAULT_ADMIN_NAMES
                if name in {staff.name for staff in rows}
            ),
        }
    finally:
        db.close()


def change_administrator(name: str, *, grant: bool) -> dict:
    db = SessionLocal()
    try:
        staff = _resolve_staff(db, name)
        if grant:
            if is_administrator(staff):
                return {
                    "status": "unchanged",
                    "name": staff.name,
                    "staff_id": staff.id,
                    "is_admin": True,
                    "message": f"{staff.name} 已经是管理员。",
                }
            staff.is_admin = 1
            action = "grant-admin"
        else:
            if config.is_admin_name(staff.name):
                raise ValueError(
                    f"{staff.name} 由服务器环境变量 JX_ADMIN_NAMES 指定为管理员，"
                    "请先从 .env 中移除该姓名并重启服务。"
                )
            if not is_administrator(staff):
                return {
                    "status": "unchanged",
                    "name": staff.name,
                    "staff_id": staff.id,
                    "is_admin": False,
                    "message": f"{staff.name} 本来就不是管理员。",
                }
            if count_administrators(db) <= 1:
                raise ValueError("必须保留至少一个管理员，拒绝取消最后一个管理员权限。")
            staff.is_admin = 0
            # Sessions issued before the change still carry the admin claim.
            staff.session_version = max(1, int(staff.session_version or 0)) + 1
            action = "revoke-admin"
        _audit(db, staff, action)
        db.commit()
        return {
            "status": "granted" if grant else "revoked",
            "name": staff.name,
            "staff_id": staff.id,
            "is_admin": grant,
            "data_root": str(config.USER_DATA_ROOT),
            "message": (
                f"{staff.name} 已获得管理员权限，重新登录后即可看到“系统管理”。"
                if grant
                else f"{staff.name} 的管理员权限已取消，其在线会话需要重新登录。"
            ),
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在服务器本机命令行恢复或调整管理员权限（管理页不可用时的唯一入口）。"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="列出当前全部管理员")
    group.add_argument("--grant", metavar="姓名", help="授予某人管理员权限")
    group.add_argument("--revoke", metavar="姓名", help="取消某人管理员权限")
    parser.add_argument(
        "--json", action="store_true", help="只输出 JSON，便于脚本化处理"
    )
    args = parser.parse_args()

    print(f"数据目录：{config.USER_DATA_ROOT}", file=sys.stderr)
    print(f"数据库文件：{config.DATABASE_PATH}", file=sys.stderr)
    if not _database_is_present():
        # Refuse instead of migrating: connecting to SQLite here would create an
        # empty database in the active root, and an empty database is exactly what
        # stops the startup rescue from adopting the legacy directory.  That would
        # turn a wrong path into permanent data loss.
        print("数据库文件不存在或为空，已拒绝操作（不会新建空库）。", file=sys.stderr)
        print(
            "请先确认数据目录：python backend/scripts/data_location.py",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # The flag lives in the database, so an old schema must be migrated first or
    # every query below would fail on a server that has not restarted since the
    # upgrade - which is the normal state right after pulling new code.
    initialize_database()

    if args.list:
        result = list_administrators()
    elif args.grant:
        result = change_administrator(args.grant, grant=True)
    else:
        result = change_administrator(args.revoke, grant=False)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    if result.get("status") == "listed":
        rows = result["administrators"]
        if rows:
            print("当前管理员：")
            for row in rows:
                origin = "环境变量指定" if row["from_env"] else "数据库记录"
                print(f"  - {row['name']}（{origin}）")
        else:
            print("当前管理员：（无）")
            recovery = result.get("recovery_admin_names") or []
            if recovery:
                print(
                    "重启服务后会按配置名单自动提升："
                    + "、".join(recovery)
                    + "，并回收名单外人员的管理员权限"
                    + "（可用 JX_DEFAULT_ADMIN_NAMES 调整或置空关闭）"
                )
            else:
                print("没有任何管理员，管理页与备份恢复入口均不可用。")
                print("请执行：python backend/scripts/manage_admin.py --grant 姓名")
        if result["env_admin_names"]:
            print("环境变量 JX_ADMIN_NAMES 指定：" + "、".join(result["env_admin_names"]))
        return
    print(result.get("message", ""))


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"操作被拒绝：{exc}", file=sys.stderr)
        raise SystemExit(2)
