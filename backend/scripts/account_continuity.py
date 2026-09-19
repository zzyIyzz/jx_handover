"""Preserve existing account credentials across an application upgrade."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config


_FIELDS = (
    "id",
    "name",
    "password_hash",
    "must_change_password",
    "password_updated_at",
)


def _staff_columns(connection: sqlite3.Connection) -> set[str]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='staff'"
    ).fetchone()
    if not table:
        return set()
    return {row[1] for row in connection.execute("PRAGMA table_info(staff)")}


def capture_snapshot(database: Path, target: Path) -> dict:
    payload: dict = {
        "database_path": str(database.resolve()),
        "supported": False,
        "accounts": [],
    }
    if database.is_file():
        connection = sqlite3.connect(str(database), timeout=30)
        try:
            if set(_FIELDS).issubset(_staff_columns(connection)):
                payload["supported"] = True
                rows = connection.execute(
                    "SELECT id, name, password_hash, must_change_password, "
                    "password_updated_at FROM staff "
                    "WHERE COALESCE(password_hash, '') <> '' ORDER BY id"
                ).fetchall()
                payload["accounts"] = [
                    {
                        "id": str(row[0]),
                        "name": str(row[1] or ""),
                        "password_hash": str(row[2] or ""),
                        "must_change_password": int(row[3] or 0),
                        "password_updated_at": row[4],
                    }
                    for row in rows
                ]
        finally:
            connection.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def verify_and_restore(database: Path, snapshot_path: Path) -> dict:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    accounts = snapshot.get("accounts") or []
    if not snapshot.get("supported"):
        return {"supported": False, "checked": 0, "restored": 0}
    if not database.is_file():
        raise RuntimeError("升级后正式数据库不存在，无法核对账号密码。")

    connection = sqlite3.connect(str(database), timeout=30)
    try:
        if not set(_FIELDS).issubset(_staff_columns(connection)):
            raise RuntimeError("升级后 staff 表缺少账号密码字段。")
        current = {
            str(row[0]): row
            for row in connection.execute(
                "SELECT id, name, password_hash, must_change_password, "
                "password_updated_at FROM staff"
            ).fetchall()
        }
        missing = [
            before.get("name") or str(before["id"])
            for before in accounts
            if str(before["id"]) not in current
        ]
        if missing:
            raise RuntimeError(
                "升级后缺少原有账号：" + "、".join(missing)
                + "；请使用本次升级备份核查。"
            )

        restored = 0
        connection.execute("BEGIN IMMEDIATE")
        for before in accounts:
            row = current[str(before["id"])]
            expected = (
                before["password_hash"],
                int(before["must_change_password"]),
                before.get("password_updated_at"),
            )
            actual = (str(row[2] or ""), int(row[3] or 0), row[4])
            if actual == expected:
                continue
            connection.execute(
                "UPDATE staff SET password_hash=?, must_change_password=?, "
                "password_updated_at=? WHERE id=?",
                (*expected, row[0]),
            )
            restored += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"supported": True, "checked": len(accounts), "restored": restored}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("--snapshot", required=True, type=Path)
    arguments = parser.parse_args()

    if arguments.mode == "snapshot":
        result = capture_snapshot(config.DATABASE_PATH, arguments.snapshot)
        print(json.dumps({
            "supported": result["supported"],
            "accounts": len(result["accounts"]),
        }, ensure_ascii=False))
        return
    print(json.dumps(
        verify_and_restore(config.DATABASE_PATH, arguments.snapshot),
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
