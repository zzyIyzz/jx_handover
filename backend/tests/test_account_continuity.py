"""Upgrade-time credential continuity checks."""
from pathlib import Path
import sqlite3
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.account_continuity import capture_snapshot, verify_and_restore


def _database(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT, "
            "password_hash TEXT, must_change_password INTEGER, "
            "password_updated_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO staff VALUES (?, ?, ?, ?, ?)",
            [
                (1, "管理员", "$argon2id$custom-admin", 0, "2026-09-19T08:30:00+08:00"),
                (2, "值班员", "$argon2id$custom-user", 0, "2026-09-19T08:35:00+08:00"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_verify_restores_changed_password_fields(tmp_path):
    database = tmp_path / "handover.db"
    snapshot = tmp_path / "account-credentials-before.json"
    _database(database)
    captured = capture_snapshot(database, snapshot)
    assert captured["supported"] is True
    assert len(captured["accounts"]) == 2

    connection = sqlite3.connect(str(database))
    try:
        connection.execute(
            "UPDATE staff SET password_hash='reset', must_change_password=1, "
            "password_updated_at=NULL WHERE id=1"
        )
        connection.commit()
    finally:
        connection.close()

    result = verify_and_restore(database, snapshot)
    assert result == {"supported": True, "checked": 2, "restored": 1}
    connection = sqlite3.connect(str(database))
    try:
        restored = connection.execute(
            "SELECT password_hash, must_change_password, password_updated_at "
            "FROM staff WHERE id=1"
        ).fetchone()
    finally:
        connection.close()
    assert restored == (
        "$argon2id$custom-admin",
        0,
        "2026-09-19T08:30:00+08:00",
    )


def test_verify_rejects_missing_existing_account(tmp_path):
    database = tmp_path / "handover.db"
    snapshot = tmp_path / "account-credentials-before.json"
    _database(database)
    capture_snapshot(database, snapshot)
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("DELETE FROM staff WHERE id=2")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="缺少原有账号"):
        verify_and_restore(database, snapshot)
