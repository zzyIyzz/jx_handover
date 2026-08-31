"""Consistent local SQLite snapshots with optional completed-copy to a NAS."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from app import config
from app.db import engine
from app.models import now_iso


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_source_path() -> Path:
    if engine.url.get_backend_name() != "sqlite":
        raise RuntimeError("当前数据库不是 SQLite，请使用数据库平台的备份工具。")
    database = engine.url.database
    if not database or database == ":memory:":
        raise RuntimeError("内存数据库不能创建持久化备份。")
    return Path(database).resolve()


def _verify_sqlite(path: Path) -> None:
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise RuntimeError("SQLite 备份完整性检查未通过。")


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _copy_verified_atomic(source: Path, target: Path, digest: str) -> None:
    """Expose the final NAS filename only after copy and verification finish."""
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        shutil.copy2(source, temporary)
        if _sha256(temporary) != digest:
            raise RuntimeError("NAS 备份复制后校验值不一致。")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def create_database_backup(*, reason: str = "manual") -> dict:
    source = _sqlite_source_path()
    if not source.exists():
        raise RuntimeError("数据库尚未创建。")

    local_dir = config.SNAPSHOT_DIR / "database_backups"
    local_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    final_path = local_dir / f"handover_v0.4.0_{reason}_{stamp}.db"
    temporary = final_path.with_suffix(".db.tmp")

    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    temporary.replace(final_path)
    _verify_sqlite(final_path)
    digest = _sha256(final_path)

    manifest = {
        "created_at": now_iso(),
        "reason": reason,
        "database_file": final_path.name,
        "sha256": digest,
        "size": final_path.stat().st_size,
        "nas_path": None,
        "nas_error": "",
    }
    if config.NAS_BACKUP_DIR:
        try:
            nas_root = Path(config.NAS_BACKUP_DIR)
            nas_dir = nas_root / datetime.now().strftime("%Y-%m")
            nas_dir.mkdir(parents=True, exist_ok=True)
            nas_target = nas_dir / final_path.name
            _copy_verified_atomic(final_path, nas_target, digest)
            manifest["nas_path"] = str(nas_target)
        except Exception as exc:  # noqa: BLE001 - local backup remains valid
            manifest["nas_error"] = str(exc)

    manifest_path = final_path.with_suffix(".json")
    _write_json_atomic(manifest_path, manifest)
    if manifest["nas_path"]:
        try:
            nas_manifest = Path(manifest["nas_path"]).with_suffix(".json")
            _copy_verified_atomic(
                manifest_path, nas_manifest, _sha256(manifest_path)
            )
        except Exception as exc:  # noqa: BLE001 - database copy is still valid
            manifest["nas_error"] = f"清单复制失败：{exc}"
            _write_json_atomic(manifest_path, manifest)
    return {**manifest, "local_path": str(final_path), "manifest_path": str(manifest_path)}


def maybe_daily_backup() -> dict | None:
    local_dir = config.SNAPSHOT_DIR / "database_backups"
    prefix = f"handover_v0.4.0_daily_{datetime.now():%Y%m%d}-"
    if local_dir.exists() and any(
        path.name.startswith(prefix) and path.suffix == ".db"
        for path in local_dir.iterdir()
    ):
        return None
    return create_database_backup(reason="daily")
