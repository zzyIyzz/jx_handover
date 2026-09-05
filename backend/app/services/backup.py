"""Verified full backups, NAS replication and restart-safe restore support.

The live SQLite database always remains on the server's local fixed disk. A
backup is first completed and verified locally, then an immutable ZIP and its
manifest may be copied to a NAS. A NAS outage therefore never blocks normal
handover work and never leaves a half-visible backup file.
"""
from __future__ import annotations

from datetime import datetime
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile

from app import config
from app.db import engine
from app.models import now_iso


BACKUP_FORMAT = 2
BACKUP_VERSION = config.APP_VERSION
FULL_BACKUP_DIRNAME = "full_backups"
RESTORE_DIRNAME = "restore"


def _short_temporary_path(parent: Path, *, suffix: str = ".tmp") -> Path:
    """Return a collision-resistant temporary name without repeating long names.

    Repeating the final filename in a hidden ``.partial`` name easily pushes
    otherwise valid Chinese Windows paths over the legacy MAX_PATH boundary.
    The temporary file lives beside the final file so ``os.replace`` remains
    atomic, but its leaf name stays deliberately short.
    """
    return parent / f".jx-{uuid.uuid4().hex[:10]}{suffix}"


def _create_backup_workspace() -> Path:
    """Create staging under the shortest usable local root.

    A short folder below the configured data disk avoids consuming the system
    disk for large sites.  When the configured data root itself is unusually
    long, the Windows temp root wins instead, preserving compatibility with
    long Chinese business filenames.
    """
    candidates = [config.USER_DATA_ROOT / ".b", Path(tempfile.gettempdir())]
    last_error: OSError | None = None
    for parent in sorted(candidates, key=lambda value: len(str(value))):
        try:
            parent.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="jxb-", dir=parent))
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"无法创建完整备份临时工作区：{last_error}")


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
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise RuntimeError("SQLite 备份完整性检查未通过。")


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _short_temporary_path(path.parent, suffix=".json.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_verified_atomic(source: Path, target: Path, digest: str) -> None:
    """Expose the final filename only after copy and verification finish."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _short_temporary_path(target.parent)
    try:
        shutil.copy2(source, temporary)
        if _sha256(temporary) != digest:
            raise RuntimeError("备份复制后 SHA256 校验值不一致。")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_stable_file(source: Path, target: Path, *, attempts: int = 3) -> dict:
    """Copy a business file and reject a copy taken during a concurrent write."""
    last_error: Exception | None = None
    for _attempt in range(attempts):
        try:
            before = source.stat()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = _short_temporary_path(target.parent)
            try:
                shutil.copy2(source, temporary)
                source_digest = _sha256(source)
                copied_digest = _sha256(temporary)
                after = source.stat()
                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or source_digest != copied_digest
                ):
                    raise RuntimeError("源文件在备份过程中发生变化。")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "size": target.stat().st_size,
                "sha256": copied_digest,
            }
        except (OSError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.08)
    raise RuntimeError(f"无法取得稳定文件副本：{source}；{last_error}")


def _online_database_copy(target: Path) -> dict:
    source = _sqlite_source_path()
    if not source.exists():
        raise RuntimeError("数据库尚未创建。")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _short_temporary_path(target.parent, suffix=".db.tmp")
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    try:
        _verify_sqlite(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": "data/handover.db",
        "category": "database",
        "size": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _sanitized_configuration() -> dict:
    return {
        "application_version": BACKUP_VERSION,
        "mode": config.APP_MODE,
        "port": config.APP_PORT,
        "public_host": config.PUBLIC_HOST,
        "auth_required": config.AUTH_REQUIRED,
        "ai_mode": config.AI_MODE,
        "ai_model": config.QWEN_MODEL if config.AI_MODE == "qwen" else "mock",
        "nas_configured": bool(config.NAS_BACKUP_DIR),
        "secrets_included": False,
        "note": "API Key、访问口令、会话签名密钥和完整 NAS 路径未写入备份。",
    }


def _iter_business_files() -> list[tuple[str, Path, Path]]:
    result: list[tuple[str, Path, Path]] = []
    for category, root in (
        ("imports", config.IMPORT_DIR),
        ("generated", config.GENERATED_DIR),
    ):
        if not root.exists():
            continue
        for source in sorted(path for path in root.rglob("*") if path.is_file()):
            result.append((category, source, source.relative_to(root)))
    return result


def _backup_root() -> Path:
    return config.SNAPSHOT_DIR / FULL_BACKUP_DIRNAME


def _restore_root() -> Path:
    return config.SNAPSHOT_DIR / RESTORE_DIRNAME


def _manifest_path_for(bundle: Path) -> Path:
    return bundle.with_suffix(".json")


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"备份清单无法读取：{path.name}；{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"备份清单格式无效：{path.name}")
    return value


def _safe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return (
        bool(normalized)
        and not normalized.startswith(("/", "\\"))
        and not path.is_absolute()
        and not (len(normalized) >= 2 and normalized[1] == ":")
        and ".." not in path.parts
    )


def _hash_archive_member(archive: zipfile.ZipFile, name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "r") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _add_tree_to_manifest(staging: Path, file_entries: list[dict]) -> None:
    for category, source, relative in _iter_business_files():
        archive_relative = Path(category) / relative
        copied = _copy_stable_file(source, staging / archive_relative)
        copied["path"] = archive_relative.as_posix()
        copied["category"] = category
        file_entries.append(copied)


def _make_backup_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def create_full_backup(
    *,
    reason: str = "manual",
    replicate: bool = True,
) -> dict:
    """Create one self-contained, verified business-data backup ZIP."""
    backup_id = _make_backup_id()
    backup_root = _backup_root()
    backup_root.mkdir(parents=True, exist_ok=True)
    # Avoid adding the long snapshot directory to every imported/generated
    # relative path. The helper prefers a short workspace on the data disk and
    # falls back to the shorter system temp root for unusually long data roots.
    staging = _create_backup_workspace()
    bundle = backup_root / f"jx-handover-backup-{backup_id}.zip"
    manifest_path = _manifest_path_for(bundle)
    file_entries: list[dict] = []
    try:
        file_entries.append(_online_database_copy(staging / "data" / "handover.db"))
        _add_tree_to_manifest(staging, file_entries)

        metadata_dir = staging / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(metadata_dir / "sanitized-config.json", _sanitized_configuration())
        config_entry = {
            "path": "metadata/sanitized-config.json",
            "category": "metadata",
            "size": (metadata_dir / "sanitized-config.json").stat().st_size,
            "sha256": _sha256(metadata_dir / "sanitized-config.json"),
        }
        file_entries.append(config_entry)

        internal_manifest = {
            "backup_format": BACKUP_FORMAT,
            "backup_id": backup_id,
            "created_at": now_iso(),
            "reason": reason,
            "application_version": BACKUP_VERSION,
            "files": file_entries,
            "file_count": len(file_entries),
            "payload_bytes": sum(int(item["size"]) for item in file_entries),
        }
        _write_json_atomic(metadata_dir / "manifest.json", internal_manifest)

        temporary_bundle = _short_temporary_path(backup_root, suffix=".zip.tmp")
        try:
            with zipfile.ZipFile(
                temporary_bundle,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                for source in sorted(
                    path for path in staging.rglob("*") if path.is_file()
                ):
                    archive.write(source, source.relative_to(staging).as_posix())
            with zipfile.ZipFile(temporary_bundle, "r") as archive:
                broken = archive.testzip()
                if broken:
                    raise RuntimeError(f"备份 ZIP 内部校验失败：{broken}")
            os.replace(temporary_bundle, bundle)
        finally:
            temporary_bundle.unlink(missing_ok=True)

        digest = _sha256(bundle)
        manifest = {
            **internal_manifest,
            "bundle_file": bundle.name,
            "bundle_size": bundle.stat().st_size,
            "bundle_sha256": digest,
            "verification": "verified",
            "verified_at": now_iso(),
            "nas_state": "not_configured" if not config.NAS_BACKUP_DIR else "pending",
            "nas_path": None,
            "nas_error": "",
            "nas_attempts": 0,
            "last_nas_attempt_at": None,
        }
        _write_json_atomic(manifest_path, manifest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if replicate and config.NAS_BACKUP_DIR:
        manifest = replicate_backup(backup_id)
    return _public_backup_result(manifest, manifest_path)


def _find_backup(backup_id: str) -> tuple[Path, Path, dict]:
    allowed = "0123456789-abcdef"
    if not backup_id or any(character not in allowed for character in backup_id.lower()):
        raise ValueError("备份编号格式无效。")
    manifests = list(
        _backup_root().glob(f"jx-handover-backup-{backup_id}.json")
    )
    if len(manifests) != 1:
        raise FileNotFoundError("未找到指定的本地备份。")
    manifest_path = manifests[0]
    manifest = _read_manifest(manifest_path)
    if str(manifest.get("backup_id") or "") != backup_id:
        raise RuntimeError("备份编号与清单不一致。")
    bundle = manifest_path.with_suffix(".zip")
    return bundle, manifest_path, manifest


def verify_full_backup(backup_id: str) -> dict:
    bundle, manifest_path, manifest = _find_backup(backup_id)
    if not bundle.is_file():
        raise RuntimeError("备份 ZIP 文件缺失。")
    expected_bundle_hash = str(manifest.get("bundle_sha256") or "")
    actual_bundle_hash = _sha256(bundle)
    if not expected_bundle_hash or actual_bundle_hash != expected_bundle_hash:
        raise RuntimeError("备份 ZIP 的 SHA256 与清单不一致。")

    with zipfile.ZipFile(bundle, "r") as archive:
        names = archive.namelist()
        if any(not _safe_zip_member(name) for name in names):
            raise RuntimeError("备份 ZIP 包含不安全路径。")
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"备份 ZIP 内部文件损坏：{broken}")
        try:
            internal = json.loads(
                archive.read("metadata/manifest.json").decode("utf-8")
            )
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("备份 ZIP 缺少有效的内部清单。") from exc
        if internal.get("backup_id") != backup_id:
            raise RuntimeError("备份 ZIP 内外清单编号不一致。")
        entries = internal.get("files")
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("备份 ZIP 的文件清单为空。")
        listed_paths = {
            str(item.get("path") or "")
            for item in entries
            if isinstance(item, dict)
        }
        if "data/handover.db" not in listed_paths:
            raise RuntimeError("备份 ZIP 缺少正式数据库。")
        for item in entries:
            name = str(item.get("path") or "")
            if not _safe_zip_member(name):
                raise RuntimeError("备份文件清单包含不安全路径。")
            try:
                digest, size = _hash_archive_member(archive, name)
            except KeyError as exc:
                raise RuntimeError(f"备份 ZIP 缺少清单文件：{name}") from exc
            if size != int(item.get("size", -1)):
                raise RuntimeError(f"备份文件大小不一致：{name}")
            if digest != str(item.get("sha256") or ""):
                raise RuntimeError(f"备份文件 SHA256 不一致：{name}")

        verify_dir = Path(tempfile.mkdtemp(prefix="jxv-"))
        try:
            database = verify_dir / "handover.db"
            with archive.open("data/handover.db", "r") as source, database.open(
                "wb"
            ) as target:
                shutil.copyfileobj(source, target)
            _verify_sqlite(database)
        finally:
            shutil.rmtree(verify_dir, ignore_errors=True)

    manifest["verification"] = "verified"
    manifest["verified_at"] = now_iso()
    _write_json_atomic(manifest_path, manifest)
    return _public_backup_result(manifest, manifest_path)


def _nas_target_paths(manifest: dict) -> tuple[Path, Path]:
    if not config.NAS_BACKUP_DIR:
        raise RuntimeError("当前未配置 NAS/云盘备份目录。")
    created = str(manifest.get("created_at") or "")
    try:
        month = datetime.fromisoformat(created).strftime("%Y-%m")
    except ValueError:
        month = datetime.now().strftime("%Y-%m")
    root = Path(config.NAS_BACKUP_DIR) / month
    bundle_name = str(manifest.get("bundle_file") or "")
    return root / bundle_name, root / Path(bundle_name).with_suffix(".json").name


def replicate_backup(backup_id: str) -> dict:
    bundle, manifest_path, manifest = _find_backup(backup_id)
    if not config.NAS_BACKUP_DIR:
        manifest["nas_state"] = "not_configured"
        manifest["nas_path"] = None
        manifest["nas_error"] = ""
        _write_json_atomic(manifest_path, manifest)
        return manifest
    attempt_number = int(manifest.get("nas_attempts") or 0) + 1
    attempt_at = now_iso()
    try:
        verify_full_backup(backup_id)
        manifest = _read_manifest(manifest_path)
        manifest["nas_attempts"] = attempt_number
        manifest["last_nas_attempt_at"] = attempt_at
        nas_bundle, nas_manifest = _nas_target_paths(manifest)
        _copy_verified_atomic(bundle, nas_bundle, str(manifest["bundle_sha256"]))
        manifest["nas_state"] = "synced"
        manifest["nas_path"] = str(nas_bundle)
        manifest["nas_error"] = ""
        manifest["nas_synced_at"] = now_iso()
        _write_json_atomic(manifest_path, manifest)
        _copy_verified_atomic(manifest_path, nas_manifest, _sha256(manifest_path))
    except Exception as exc:  # noqa: BLE001 - local backup remains valid
        manifest["nas_attempts"] = attempt_number
        manifest["last_nas_attempt_at"] = attempt_at
        manifest["nas_state"] = "pending"
        manifest["nas_path"] = None
        manifest["nas_error"] = str(exc)
        _write_json_atomic(manifest_path, manifest)
    return manifest


def replicate_pending_backups(*, limit: int = 10) -> dict:
    attempted = 0
    synced = 0
    failed = 0
    for item in list_full_backups():
        if attempted >= max(1, min(limit, 100)):
            break
        if item.get("nas_state") == "synced":
            continue
        if not config.NAS_BACKUP_DIR:
            break
        attempted += 1
        result = replicate_backup(str(item["backup_id"]))
        if result.get("nas_state") == "synced":
            synced += 1
        else:
            failed += 1
    return {"attempted": attempted, "synced": synced, "failed": failed}


def _public_backup_result(manifest: dict, manifest_path: Path) -> dict:
    bundle = manifest_path.with_suffix(".zip")
    return {
        **manifest,
        "local_path": str(bundle),
        "manifest_path": str(manifest_path),
        # Compatibility keys retained while clients upgrade from V0.4.0.
        "database_file": str(manifest.get("bundle_file") or bundle.name),
        "sha256": str(manifest.get("bundle_sha256") or ""),
        "size": int(manifest.get("bundle_size") or 0),
        "nas_error": str(manifest.get("nas_error") or ""),
    }


def list_full_backups() -> list[dict]:
    rows: list[dict] = []
    root = _backup_root()
    if not root.exists():
        return rows
    for manifest_path in root.glob("jx-handover-backup-*.json"):
        try:
            manifest = _read_manifest(manifest_path)
            bundle = manifest_path.with_suffix(".zip")
            manifest["local_present"] = bundle.is_file()
            manifest["local_path"] = str(bundle)
            manifest["manifest_path"] = str(manifest_path)
            rows.append(manifest)
        except RuntimeError as exc:
            rows.append(
                {
                    "backup_id": manifest_path.stem.removeprefix(
                        "jx-handover-backup-"
                    ),
                    "created_at": "",
                    "reason": "unknown",
                    "verification": "invalid_manifest",
                    "local_present": False,
                    "nas_state": "unknown",
                    "nas_error": str(exc),
                    "manifest_path": str(manifest_path),
                }
            )
    return sorted(
        rows, key=lambda item: str(item.get("created_at") or ""), reverse=True
    )


def backup_status() -> dict:
    backups = list_full_backups()
    latest = backups[0] if backups else None
    latest_synced = next(
        (item for item in backups if item.get("nas_state") == "synced"), None
    )
    pending = sum(1 for item in backups if item.get("nas_state") == "pending")
    return {
        "total": len(backups),
        "pending_nas": pending,
        "latest_local_at": latest.get("created_at") if latest else None,
        "latest_local_id": latest.get("backup_id") if latest else None,
        "latest_nas_at": latest_synced.get("nas_synced_at") if latest_synced else None,
        "latest_nas_id": latest_synced.get("backup_id") if latest_synced else None,
        "nas_configured": bool(config.NAS_BACKUP_DIR),
    }


def service_identity() -> str:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["whoami.exe"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return getpass.getuser()


def test_nas_access() -> dict:
    identity = service_identity()
    if not config.NAS_BACKUP_DIR:
        return {
            "configured": False,
            "ok": False,
            "identity": identity,
            "path": "",
            "latency_ms": None,
            "message": "未配置 NAS/云盘备份目录。",
        }
    root = Path(config.NAS_BACKUP_DIR)
    started = time.perf_counter()
    probe = root / f".jx-probe-{uuid.uuid4().hex[:8]}.tmp"
    renamed = probe.with_suffix(".verified")
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = os.urandom(64)
        probe.write_bytes(payload)
        os.replace(probe, renamed)
        if renamed.read_bytes() != payload:
            raise OSError("共享盘写入后读取内容不一致。")
        renamed.unlink()
        return {
            "configured": True,
            "ok": True,
            "identity": identity,
            "path": str(root),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": "当前服务器进程身份可创建、改名、读取和删除文件。",
        }
    except OSError as exc:
        return {
            "configured": True,
            "ok": False,
            "identity": identity,
            "path": str(root),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": str(exc),
        }
    finally:
        probe.unlink(missing_ok=True)
        renamed.unlink(missing_ok=True)


def pending_restore_status() -> dict | None:
    marker = _restore_root() / "pending.json"
    if not marker.is_file():
        return None
    try:
        return _read_manifest(marker)
    except RuntimeError as exc:
        return {"state": "invalid", "error": str(exc)}


def last_restore_result() -> dict | None:
    path = _restore_root() / "last-result.json"
    if not path.is_file():
        return None
    try:
        return _read_manifest(path)
    except RuntimeError as exc:
        return {"state": "invalid", "error": str(exc)}


def schedule_restore(backup_id: str, *, requested_by: str) -> dict:
    verified = verify_full_backup(backup_id)
    marker = _restore_root() / "pending.json"
    if marker.exists():
        existing = pending_restore_status() or {}
        raise RuntimeError(
            f"已有待执行恢复：{existing.get('backup_id') or '未知编号'}。"
            "请先重启服务器或取消。"
        )
    request = {
        "state": "pending_restart",
        "backup_id": backup_id,
        "bundle_path": verified["local_path"],
        "bundle_sha256": verified["bundle_sha256"],
        "requested_by": requested_by,
        "requested_at": now_iso(),
        "instruction": (
            "请在宝塔终端执行 docker compose restart app，恢复会在 Web 服务启动前执行。"
            if config.APP_MODE == "cloud"
            else "请在服务器控制器中点击“重启服务器”，恢复会在 Web 服务启动前执行。"
        ),
    }
    _write_json_atomic(marker, request)
    return request


def cancel_scheduled_restore() -> dict:
    marker = _restore_root() / "pending.json"
    existed = marker.exists()
    marker.unlink(missing_ok=True)
    return {"cancelled": existed}


def _extract_restore_payload(bundle: Path, target: Path) -> None:
    with zipfile.ZipFile(bundle, "r") as archive:
        for info in archive.infolist():
            if not _safe_zip_member(info.filename):
                raise RuntimeError("备份 ZIP 包含不安全路径。")
            destination = target / Path(info.filename.replace("\\", "/"))
            resolved = destination.resolve()
            try:
                resolved.relative_to(target.resolve())
            except ValueError as exc:
                raise RuntimeError("备份 ZIP 路径越界。") from exc
            if info.is_dir():
                resolved.mkdir(parents=True, exist_ok=True)
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, resolved.open("wb") as output:
                shutil.copyfileobj(source, output)


def _rewrite_snapshot_paths(database: Path) -> int:
    connection = sqlite3.connect(str(database), timeout=30)
    updated = 0
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='document_snapshots'"
        ).fetchone()
        if not table:
            return 0
        rows = connection.execute(
            "SELECT id, docx_path FROM document_snapshots"
        ).fetchall()
        generated_files = [
            path for path in config.GENERATED_DIR.rglob("*") if path.is_file()
        ]
        by_name: dict[str, list[Path]] = {}
        for path in generated_files:
            by_name.setdefault(path.name.lower(), []).append(path)
        for snapshot_id, old_text in rows:
            old_value = str(old_text or "")
            # A Windows backup restored on Linux still contains backslashes;
            # pathlib on Linux treats them as ordinary characters.  Split both
            # separator styles so historical Word links survive the migration.
            normalized_parts = [
                part for part in re.split(r"[\\/]+", old_value) if part
            ]
            old_path = Path(*normalized_parts) if normalized_parts else Path("")
            candidate: Path | None = None
            parts = list(normalized_parts)
            index = next(
                (i for i, part in enumerate(parts) if part.lower() == "generated"),
                None,
            )
            if index is not None and index + 1 < len(parts):
                relative = Path(*parts[index + 1 :])
                mapped = config.GENERATED_DIR / relative
                if mapped.is_file():
                    candidate = mapped
            if candidate is None:
                filename = parts[-1] if parts else old_path.name
                matches = by_name.get(filename.lower(), [])
                if len(matches) == 1:
                    candidate = matches[0]
            if candidate is not None and str(candidate) != old_value:
                connection.execute(
                    "UPDATE document_snapshots SET docx_path=? WHERE id=?",
                    (str(candidate), snapshot_id),
                )
                updated += 1
        connection.commit()
    finally:
        connection.close()
    return updated


def apply_pending_restore() -> dict | None:
    """Apply a verified restore before FastAPI opens the live database."""
    marker = _restore_root() / "pending.json"
    if not marker.is_file():
        return None
    request = _read_manifest(marker)
    backup_id = str(request.get("backup_id") or "")
    verify_full_backup(backup_id)
    bundle, _manifest_path, _manifest = _find_backup(backup_id)

    # This is the last safe point before any live path changes. A replacement
    # server may legitimately have no database yet; in that disaster-recovery
    # case there is nothing to preserve before installing the imported set.
    pre_restore = (
        create_full_backup(reason="pre-restore", replicate=False)
        if config.DATABASE_PATH.is_file()
        else None
    )
    # Restore moves must stay on the same volume as the live data. Use short
    # roots directly below USER_DATA_ROOT so long generated filenames remain
    # usable on Windows during both install and rollback.
    staging = config.USER_DATA_ROOT / ".r" / f"s-{uuid.uuid4().hex[:8]}"
    rollback = config.USER_DATA_ROOT / ".r" / (
        f"b-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    )
    staging.mkdir(parents=True)
    rollback.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        _extract_restore_payload(bundle, staging)
        prepared_database = staging / "data" / "handover.db"
        _verify_sqlite(prepared_database)
        engine.dispose()

        live_database = config.DATABASE_PATH
        live_database.parent.mkdir(parents=True, exist_ok=True)
        for current in (
            live_database,
            Path(f"{live_database}-wal"),
            Path(f"{live_database}-shm"),
        ):
            if current.exists():
                destination = rollback / "data" / current.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, destination)
                moved.append((destination, current))
        for category, live_directory in (
            ("imports", config.IMPORT_DIR),
            ("generated", config.GENERATED_DIR),
        ):
            if live_directory.exists():
                destination = rollback / category
                os.replace(live_directory, destination)
                moved.append((destination, live_directory))

        os.replace(prepared_database, live_database)
        installed.append(live_database)
        for category, live_directory in (
            ("imports", config.IMPORT_DIR),
            ("generated", config.GENERATED_DIR),
        ):
            prepared = staging / category
            if prepared.exists():
                os.replace(prepared, live_directory)
            else:
                live_directory.mkdir(parents=True, exist_ok=True)
            installed.append(live_directory)

        rewritten = _rewrite_snapshot_paths(live_database)
        _verify_sqlite(live_database)
        result = {
            **request,
            "state": "completed",
            "completed_at": now_iso(),
            "pre_restore_backup_id": pre_restore["backup_id"] if pre_restore else None,
            "rollback_directory": str(rollback),
            "rewritten_document_paths": rewritten,
        }
        _write_json_atomic(_restore_root() / "last-result.json", result)
        marker.unlink(missing_ok=True)
        return result
    except Exception as exc:
        logging.exception("Restore failed; rolling back current live data")
        engine.dispose()
        for installed_path in reversed(installed):
            if installed_path.is_dir():
                shutil.rmtree(installed_path, ignore_errors=True)
            else:
                installed_path.unlink(missing_ok=True)
        for source, destination in reversed(moved):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                os.replace(source, destination)
        failed = {
            **request,
            "state": "failed",
            "failed_at": now_iso(),
            "error": str(exc),
            "pre_restore_backup_id": pre_restore.get("backup_id") if pre_restore else None,
        }
        _write_json_atomic(_restore_root() / "last-result.json", failed)
        marker.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def create_database_backup(*, reason: str = "manual") -> dict:
    """Retain the V0.4.0 single-database snapshot API for migration tooling."""
    source = _sqlite_source_path()
    if not source.exists():
        raise RuntimeError("数据库尚未创建。")
    local_dir = config.SNAPSHOT_DIR / "database_backups"
    local_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    final_path = local_dir / f"handover_v{config.APP_VERSION}_{reason}_{stamp}.db"
    details = _online_database_copy(final_path)
    manifest = {
        "created_at": now_iso(),
        "reason": reason,
        "database_file": final_path.name,
        "sha256": details["sha256"],
        "size": details["size"],
        "nas_path": None,
        "nas_error": "",
    }
    if config.NAS_BACKUP_DIR:
        try:
            nas_dir = Path(config.NAS_BACKUP_DIR) / datetime.now().strftime("%Y-%m")
            nas_target = nas_dir / final_path.name
            _copy_verified_atomic(final_path, nas_target, str(details["sha256"]))
            manifest["nas_path"] = str(nas_target)
        except Exception as exc:  # noqa: BLE001 - local legacy snapshot is valid
            manifest["nas_error"] = str(exc)
    manifest_path = final_path.with_suffix(".json")
    _write_json_atomic(manifest_path, manifest)
    if manifest["nas_path"]:
        try:
            nas_manifest = Path(str(manifest["nas_path"])).with_suffix(".json")
            _copy_verified_atomic(manifest_path, nas_manifest, _sha256(manifest_path))
        except Exception as exc:  # noqa: BLE001
            manifest["nas_error"] = f"清单复制失败：{exc}"
            _write_json_atomic(manifest_path, manifest)
    return {
        **manifest,
        "local_path": str(final_path),
        "manifest_path": str(manifest_path),
    }


def maybe_daily_backup() -> dict | None:
    today = datetime.now().date().isoformat()
    for item in list_full_backups():
        if item.get("reason") != "daily":
            continue
        created = str(item.get("created_at") or "")
        if created.startswith(today):
            if config.NAS_BACKUP_DIR and item.get("nas_state") != "synced":
                replicate_pending_backups(limit=10)
            return None
    return create_full_backup(reason="daily")
