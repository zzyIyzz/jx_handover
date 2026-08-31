"""Safe one-time import of a V0.3 desktop data directory into V0.4 server data."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_database(path: Path) -> None:
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise RuntimeError(f"数据库完整性检查未通过：{path}")


def _online_copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    try:
        _verify_database(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def locate_legacy_root(selected: Path) -> Path:
    selected = selected.expanduser().resolve()
    candidates = [selected]
    if selected.name.lower() == "data":
        candidates.append(selected.parent)
    candidates.extend([selected / "runtime", selected / "JXHandover"])
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "data" / "handover.db").is_file():
            return candidate
    raise FileNotFoundError(
        "所选目录中未找到 data\\handover.db。请选择旧版 JXHandover、runtime 或项目目录。"
    )


def _same_file_content(first: Path, second: Path) -> bool:
    return (
        first.stat().st_size == second.stat().st_size
        and _sha256(first) == _sha256(second)
    )


def _copy_tree_without_overwrite(
    source: Path, target: Path, stamp: str
) -> tuple[int, dict[str, Path]]:
    if not source.exists():
        return 0, {}
    copied = 0
    destinations: dict[str, Path] = {}
    for source_file in source.rglob("*"):
        if not source_file.is_file():
            continue
        relative = source_file.relative_to(source)
        target_file = target / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if target_file.exists():
            if _same_file_content(source_file, target_file):
                destinations[relative.as_posix().lower()] = target_file
                continue
            target_file = target_file.with_name(
                f"{target_file.stem}_从V030迁移_{stamp}{target_file.suffix}"
            )
            counter = 1
            while target_file.exists():
                target_file = target_file.with_name(
                    f"{target_file.stem}_{counter}{target_file.suffix}"
                )
                counter += 1
        shutil.copy2(source_file, target_file)
        destinations[relative.as_posix().lower()] = target_file
        copied += 1
    return copied, destinations


def _find_generated_relative(
    old_value: str,
    generated_root: Path,
    migrated_destinations: dict[str, Path],
) -> Path | None:
    old_path = Path(old_value)
    parts = list(old_path.parts)
    generated_index = next(
        (index for index, part in enumerate(parts) if part.lower() == "generated"),
        None,
    )
    if generated_index is not None and generated_index + 1 < len(parts):
        relative = Path(*parts[generated_index + 1:])
        mapped = migrated_destinations.get(relative.as_posix().lower())
        if mapped is not None and mapped.is_file():
            return mapped
        candidate = generated_root / relative
        if candidate.is_file():
            return candidate
    matches = [path for path in generated_root.rglob(old_path.name) if path.is_file()]
    return matches[0] if len(matches) == 1 else None


def _rewrite_document_paths(
    database: Path,
    generated_root: Path,
    migrated_destinations: dict[str, Path],
) -> int:
    connection = sqlite3.connect(str(database), timeout=30)
    updated = 0
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_snapshots'"
        ).fetchone()
        if not table:
            return 0
        rows = connection.execute(
            "SELECT id, docx_path FROM document_snapshots"
        ).fetchall()
        for snapshot_id, old_value in rows:
            candidate = _find_generated_relative(
                str(old_value or ""), generated_root, migrated_destinations
            )
            if candidate is None:
                continue
            connection.execute(
                "UPDATE document_snapshots SET docx_path=? WHERE id=?",
                (str(candidate), snapshot_id),
            )
            updated += 1
        connection.commit()
    finally:
        connection.close()
    return updated


def migrate_v030_data(selected: Path, target_root: Path) -> dict:
    """Import desktop data while preserving a recoverable server-side backup."""
    source_root = locate_legacy_root(selected)
    target_root = target_root.expanduser().resolve()
    if source_root == target_root:
        raise ValueError("所选目录就是当前服务器数据目录，无需迁移。")

    source_db = source_root / "data" / "handover.db"
    target_db = target_root / "data" / "handover.db"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = target_root / "snapshots" / "database_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    previous_backup = None
    if target_db.exists() and target_db.stat().st_size:
        previous_backup = backup_dir / f"handover_before_v030_import_{stamp}.db"
        _online_copy_database(target_db, previous_backup)

    # Prepare and validate the source database before touching the live path.
    prepared = target_db.with_name(f".handover_v030_{uuid.uuid4().hex}.prepared")
    _online_copy_database(source_db, prepared)

    copied_files: dict[str, int] = {}
    try:
        generated_destinations: dict[str, Path] = {}
        for directory in ("imports", "generated", "snapshots"):
            copied, destinations = _copy_tree_without_overwrite(
                source_root / directory,
                target_root / directory,
                stamp,
            )
            copied_files[directory] = copied
            if directory == "generated":
                generated_destinations = destinations
        rewritten = _rewrite_document_paths(
            prepared, target_root / "generated", generated_destinations
        )
        _verify_database(prepared)

        # A cleanly stopped WAL database can leave empty sidecars. Move any
        # remaining files out of the way so they can never be applied to the
        # newly imported main database.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target_db}{suffix}")
            if sidecar.exists():
                sidecar.replace(backup_dir / f"handover_before_v030_import_{stamp}{suffix}")
        target_db.parent.mkdir(parents=True, exist_ok=True)
        os.replace(prepared, target_db)
    finally:
        prepared.unlink(missing_ok=True)

    result = {
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "target_root": str(target_root),
        "source_sha256": _sha256(source_db),
        "target_sha256": _sha256(target_db),
        "previous_server_backup": str(previous_backup) if previous_backup else None,
        "copied_files": copied_files,
        "rewritten_document_paths": rewritten,
    }
    manifest = target_root / "snapshots" / f"v030_to_v040_migration_{stamp}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary_manifest, manifest)
    result["manifest_path"] = str(manifest)
    return result


def relocate_server_data(source_root: Path, target_root: Path) -> dict:
    """Copy a stopped server's business data to another local directory.

    The source remains untouched.  Files are first copied to a sibling staging
    directory, the SQLite copy is verified, and only then is the completed
    staging directory renamed to the selected target.  Controller settings are
    intentionally updated by the caller after this function succeeds.
    """
    source_root = source_root.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if source_root == target_root:
        raise ValueError("新旧正式数据目录相同，无需迁移。")
    try:
        target_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise ValueError("新正式数据目录不能位于旧正式数据目录内部。")
    try:
        source_root.relative_to(target_root)
    except ValueError:
        pass
    else:
        raise ValueError("旧正式数据目录不能位于新正式数据目录内部。")

    source_db = source_root / "data" / "handover.db"
    if not source_db.is_file():
        raise FileNotFoundError(f"旧正式数据目录中没有数据库：{source_db}")
    if target_root.exists() and any(target_root.iterdir()):
        raise ValueError("新正式数据目录必须为空，避免与另一套数据混合。")

    staging = target_root.parent / f".{target_root.name}.jx-relocate-{uuid.uuid4().hex}"
    if staging.exists():
        raise FileExistsError(f"迁移暂存目录已存在：{staging}")
    staging.mkdir(parents=True)
    copied_files = 0
    copied_bytes = 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    business_directories = ("data", "imports", "generated", "snapshots")
    try:
        _online_copy_database(source_db, staging / "data" / "handover.db")
        copied_files += 1
        copied_bytes += (staging / "data" / "handover.db").stat().st_size

        for directory in business_directories:
            source_directory = source_root / directory
            if not source_directory.exists():
                continue
            for source_file in source_directory.rglob("*"):
                if not source_file.is_file():
                    continue
                if directory == "data" and source_file.name in {
                    "handover.db", "handover.db-wal", "handover.db-shm",
                }:
                    continue
                relative = source_file.relative_to(source_root)
                target_file = staging / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)
                if not _same_file_content(source_file, target_file):
                    raise RuntimeError(f"迁移文件校验失败：{relative}")
                copied_files += 1
                copied_bytes += target_file.stat().st_size

        _verify_database(staging / "data" / "handover.db")
        if target_root.exists():
            target_root.rmdir()
        os.replace(staging, target_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    target_db = target_root / "data" / "handover.db"
    result = {
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "target_root": str(target_root),
        "source_database_sha256": _sha256(source_db),
        "target_database_sha256": _sha256(target_db),
        "copied_files": copied_files,
        "copied_bytes": copied_bytes,
        "source_preserved": True,
    }
    manifest = target_root / "snapshots" / f"data_root_relocation_{stamp}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, manifest)
    result["manifest_path"] = str(manifest)
    return result
