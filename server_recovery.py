"""Offline import of a verified full backup from NAS to a server data root."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
import zipfile


BACKUP_FORMAT = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return (
        bool(normalized)
        and not normalized.startswith(("/", "\\"))
        and not path.is_absolute()
        and not (len(normalized) >= 2 and normalized[1] == ":")
        and ".." not in path.parts
    )


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"无法读取备份清单：{path}；{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("备份清单不是有效对象。")
    return value


def _hash_member(archive: zipfile.ZipFile, name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "r") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _verify_database_member(archive: zipfile.ZipFile, temporary_root: Path) -> None:
    database = temporary_root / "handover.db"
    with archive.open("data/handover.db", "r") as source, database.open("wb") as target:
        shutil.copyfileobj(source, target)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise RuntimeError("备份中的 SQLite 数据库完整性检查未通过。")


def verify_external_backup(
    bundle_path: Path,
    manifest_path: Path | None = None,
) -> dict:
    bundle = bundle_path.expanduser().resolve()
    manifest_file = (manifest_path or bundle.with_suffix(".json")).expanduser().resolve()
    if not bundle.is_file() or bundle.suffix.lower() != ".zip":
        raise FileNotFoundError("请选择从 NAS 复制回来的完整备份 ZIP。")
    if not manifest_file.is_file():
        raise FileNotFoundError(
            f"缺少同名备份清单：{manifest_file.name}。ZIP 与 JSON 必须成对保留。"
        )
    manifest = _read_json(manifest_file)
    backup_id = str(manifest.get("backup_id") or "").strip()
    if not backup_id:
        raise RuntimeError("备份清单缺少 backup_id。")
    if int(manifest.get("backup_format") or 0) != BACKUP_FORMAT:
        raise RuntimeError("备份格式不受当前版本支持。")
    if str(manifest.get("bundle_file") or "") != bundle.name:
        raise RuntimeError("ZIP 文件名与备份清单不一致。")
    actual_bundle_hash = _sha256(bundle)
    if actual_bundle_hash != str(manifest.get("bundle_sha256") or "").lower():
        raise RuntimeError("完整备份 ZIP 的 SHA256 与清单不一致。")

    temporary_root = Path(tempfile.mkdtemp(prefix="jx-recovery-verify-"))
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            names = archive.namelist()
            if any(not _safe_member(name) for name in names):
                raise RuntimeError("完整备份 ZIP 包含不安全路径。")
            broken = archive.testzip()
            if broken:
                raise RuntimeError(f"完整备份 ZIP 内部损坏：{broken}")
            try:
                internal = json.loads(
                    archive.read("metadata/manifest.json").decode("utf-8")
                )
            except (KeyError, ValueError, UnicodeDecodeError) as exc:
                raise RuntimeError("完整备份 ZIP 缺少有效内部清单。") from exc
            if internal.get("backup_id") != backup_id:
                raise RuntimeError("完整备份的内外清单编号不一致。")
            entries = internal.get("files")
            if not isinstance(entries, list) or not entries:
                raise RuntimeError("完整备份的文件清单为空。")
            for item in entries:
                if not isinstance(item, dict):
                    raise RuntimeError("完整备份文件清单格式无效。")
                name = str(item.get("path") or "")
                if not _safe_member(name) or name not in names:
                    raise RuntimeError(f"完整备份缺少或拒绝文件：{name}")
                digest, size = _hash_member(archive, name)
                if size != int(item.get("size", -1)):
                    raise RuntimeError(f"完整备份文件大小不一致：{name}")
                if digest != str(item.get("sha256") or ""):
                    raise RuntimeError(f"完整备份文件 SHA256 不一致：{name}")
            _verify_database_member(archive, temporary_root)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    return {
        **manifest,
        "bundle_path": str(bundle),
        "manifest_path": str(manifest_file),
        "verified": True,
    }


def _copy_verified(source: Path, target: Path, expected_hash: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".jx-{uuid.uuid4().hex[:10]}.tmp"
    try:
        shutil.copy2(source, temporary)
        if _sha256(temporary) != expected_hash:
            raise RuntimeError("复制到服务器本地后 SHA256 不一致。")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def import_backup_bundle(bundle_path: Path, data_root: Path) -> dict:
    """Import a NAS backup into the local restore center without overwriting."""
    verified = verify_external_backup(bundle_path)
    data_root = data_root.expanduser().resolve()
    target_dir = data_root / "snapshots" / "full_backups"
    target_bundle = target_dir / str(verified["bundle_file"])
    target_manifest = target_bundle.with_suffix(".json")
    source_bundle = Path(str(verified["bundle_path"]))
    source_manifest = Path(str(verified["manifest_path"]))
    expected_hash = str(verified["bundle_sha256"])

    if target_bundle.exists() or target_manifest.exists():
        if (
            target_bundle.is_file()
            and target_manifest.is_file()
            and _sha256(target_bundle) == expected_hash
            and _sha256(target_manifest) == _sha256(source_manifest)
        ):
            already_imported = True
        else:
            raise FileExistsError(
                "服务器本地已有同名但内容不同的备份，已拒绝覆盖。"
            )
    else:
        _copy_verified(source_bundle, target_bundle, expected_hash)
        _copy_verified(source_manifest, target_manifest, _sha256(source_manifest))
        already_imported = False

    receipt = {
        "backup_id": verified["backup_id"],
        "bundle_file": verified["bundle_file"],
        "bundle_sha256": expected_hash,
        "local_bundle_path": str(target_bundle),
        "local_manifest_path": str(target_manifest),
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_directory": str(source_bundle.parent),
        "already_imported": already_imported,
    }
    receipt_path = data_root / "snapshots" / "restore" / (
        f"imported-{verified['backup_id']}.json"
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.parent / f".jx-{uuid.uuid4().hex[:10]}.tmp"
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, receipt_path)
    return receipt


def schedule_imported_restore(data_root: Path, imported: dict, *, requested_by: str) -> dict:
    restore_dir = data_root.expanduser().resolve() / "snapshots" / "restore"
    marker = restore_dir / "pending.json"
    if marker.exists():
        raise FileExistsError("已有待恢复任务；请先执行或取消，不能覆盖。")
    request = {
        "state": "pending_restart",
        "backup_id": imported["backup_id"],
        "bundle_path": imported["local_bundle_path"],
        "bundle_sha256": imported["bundle_sha256"],
        "requested_by": requested_by,
        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "offline-nas-import",
        "instruction": "启动服务器后会先校验并恢复；新服务器没有旧数据库时直接安装恢复集。",
    }
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.parent / f".jx-{uuid.uuid4().hex[:10]}.tmp"
    temporary.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, marker)
    return request
