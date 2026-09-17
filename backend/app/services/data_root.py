"""One fixed live-data directory, with a safe rescue for the old layout.

Historically the data root depended on ``JX_HANDOVER_MODE``: desktop runs used
``runtime`` while server/cloud runs used ``runtime-server``, and the Docker
profile bind-mounted a third path.  Starting the same checkout in another mode
therefore pointed the application at an empty folder.  Accounts and handover
records were still on disk, but the system behaved as if everything had been
lost and re-seeded a fresh roster.

This module keeps a single root, and when that root is virgin it copies an
existing database in from a known legacy location.  The rescue only ever reads
and copies; it never deletes, moves or overwrites operator data.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app import config


logger = logging.getLogger(__name__)

#: Sub-directories that together make up one deployment's live data.
DATA_SUBDIRECTORIES = ("data", "imports", "generated", "snapshots", "logs")

ADOPTION_MARKER_NAME = "adopted_from.json"


def database_path(root: Path) -> Path:
    return Path(root) / "data" / "handover.db"


def has_database(root: Path) -> bool:
    candidate = database_path(root)
    try:
        return candidate.is_file() and candidate.stat().st_size > 0
    except OSError:
        return False


def _database_url_overridden() -> bool:
    return bool(os.getenv("JX_DATABASE_URL", "").strip())


def candidate_roots() -> list[Path]:
    """Legacy roots that may still hold the only real database."""
    active = Path(config.USER_DATA_ROOT).resolve()
    found: list[Path] = []
    seen: set[Path] = {active}
    for raw in config.LEGACY_DATA_ROOT_CANDIDATES:
        try:
            root = Path(raw).resolve()
        except OSError:
            continue
        if root in seen or not has_database(root):
            continue
        seen.add(root)
        found.append(root)
    return found


def adoption_marker_path() -> Path:
    return config.DATA_DIR / ADOPTION_MARKER_NAME


def read_adoption_report() -> dict | None:
    """Return the recorded rescue result, or ``None`` when nothing was done."""
    try:
        payload = json.loads(adoption_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_adoption_report(report: dict) -> None:
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = adoption_marker_path().with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(adoption_marker_path())
    except OSError:  # noqa: PERF203 - the rescue result must not break startup
        logger.exception("无法写入数据目录接管记录")


def _verify_sqlite(path: Path) -> str:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        return f"error: {exc}"
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        return f"error: {exc}"
    finally:
        connection.close()
    return str(row[0]).lower() if row else "unknown"


def _copy_root(source: Path, target: Path) -> tuple[list[Path], int]:
    """Copy live data without ever replacing a file that already exists."""
    copied: list[Path] = []
    total_bytes = 0
    for name in DATA_SUBDIRECTORIES:
        source_dir = source / name
        if not source_dir.is_dir():
            continue
        target_dir = target / name
        target_dir.mkdir(parents=True, exist_ok=True)
        for source_path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
            relative = source_path.relative_to(source_dir)
            destination = target_dir / relative
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            copied.append(destination)
            try:
                total_bytes += destination.stat().st_size
            except OSError:
                pass
    return copied, total_bytes


def _rollback(copied: list[Path]) -> None:
    for path in reversed(copied):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("接管失败后无法清理 %s", path)


def adoption_plan(*, source: Path | None = None) -> dict:
    """Decide what :func:`adopt_legacy_data_root` would do, without touching files.

    ``data_location.py --dry-run`` must give the same answer as the real call.
    A wrong prediction is worse than none: an operator who trusts it either
    skips a rescue that was needed or expects records that never arrive.  Both
    paths therefore share this function instead of each keeping their own copy
    of the guard order.
    """
    active = Path(config.USER_DATA_ROOT).resolve()
    candidates = [str(path) for path in candidate_roots()]
    plan: dict = {
        "state": "adopt",
        "active_root": str(active),
        "active_has_database": has_database(active),
        "candidates": candidates,
        "source": "",
        "message": "",
    }

    if plan["active_has_database"]:
        plan["state"] = "skipped"
        plan["message"] = "当前数据目录已有数据库，不需要接管。"
        return plan
    if _database_url_overridden():
        plan["state"] = "disabled"
        plan["message"] = (
            "已通过 JX_DATABASE_URL 指定数据库位置，跳过自动接管。"
        )
        return plan
    if not config.DATA_ROOT_AUTOFIND:
        plan["state"] = "disabled"
        plan["message"] = "JX_DATA_ROOT_AUTOFIND=0，已关闭自动接管。"
        return plan

    chosen = Path(source).resolve() if source else None
    if chosen is not None and not has_database(chosen):
        plan["state"] = "failed"
        plan["source"] = str(chosen)
        plan["message"] = f"{chosen} 中没有可接管的数据库。"
        return plan
    if chosen is None:
        if not candidates:
            plan["state"] = "empty"
            plan["message"] = "没有找到其他包含数据库的历史数据目录。"
            return plan
        if len(candidates) > 1:
            plan["state"] = "ambiguous"
            plan["message"] = (
                "找到多个历史数据目录：" + "、".join(candidates)
                + "。请执行 python backend/scripts/data_location.py --adopt 目录 指定其中一个。"
            )
            return plan
        chosen = Path(candidates[0])

    plan["source"] = str(chosen)
    plan["message"] = f"将从 {chosen} 复制到 {active}（只复制，原目录不改动）。"
    return plan


def adopt_legacy_data_root(*, source: Path | None = None) -> dict:
    """Copy an existing legacy database into the active root when it is empty.

    Returns a report describing what happened.  The call is a no-op when the
    active root already holds a database, when no unique legacy root exists, or
    when the operator pinned the database location explicitly.
    """
    plan = adoption_plan(source=source)
    active = Path(plan["active_root"])
    report: dict = {
        "state": "skipped" if plan["state"] == "adopt" else plan["state"],
        "active_root": plan["active_root"],
        "candidates": plan["candidates"],
        "source": plan["source"],
        "copied_files": 0,
        "copied_bytes": 0,
        "database_check": "",
        "message": plan["message"],
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    if plan["state"] != "adopt":
        if plan["state"] == "ambiguous":
            logger.warning(plan["message"])
        return report

    chosen = Path(plan["source"])
    copied, total_bytes = _copy_root(chosen, active)
    report["copied_files"] = len(copied)
    report["copied_bytes"] = total_bytes
    if not has_database(active):
        _rollback(copied)
        report["state"] = "failed"
        report["copied_files"] = 0
        report["message"] = f"从 {chosen} 复制后仍未得到数据库，已撤销本次复制。"
        logger.error(report["message"])
        return report

    check = _verify_sqlite(database_path(active))
    report["database_check"] = check
    if check != "ok":
        _rollback(copied)
        report["state"] = "failed"
        report["copied_files"] = 0
        report["message"] = (
            f"接管的数据库完整性检查未通过（{check}），已撤销复制；原目录 {chosen} 未改动。"
        )
        logger.error(report["message"])
        return report

    report["state"] = "adopted"
    report["message"] = (
        f"已从 {chosen} 复制 {len(copied)} 个文件到 {active}；原目录保持不变。"
    )
    logger.warning(
        "数据目录接管完成：%s -> %s（%d 个文件，%d 字节）。原目录未被修改或删除。",
        chosen, active, len(copied), total_bytes,
    )
    _write_adoption_report(report)
    return report


def data_root_report() -> dict:
    """Facts about the live-data location for health and management pages."""
    active = Path(config.USER_DATA_ROOT).resolve()
    adoption = read_adoption_report()
    database = database_path(active)
    size = 0
    try:
        size = database.stat().st_size if database.is_file() else 0
    except OSError:
        size = 0
    warnings: list[str] = []
    if not has_database(active):
        warnings.append(
            f"正式数据目录 {active} 中还没有数据库；首次启动会自动建立空库。"
        )
    for path in candidate_roots():
        warnings.append(
            f"另有历史数据目录 {path} 仍保存着数据库；当前服务没有使用它。"
        )
    if not config.DATA_ROOT_EXPLICIT:
        warnings.append(
            "未显式配置 JX_HANDOVER_DATA_DIR，当前使用程序默认目录；"
            "生产部署建议固定该目录，避免更换运行方式后指向空目录。"
        )
    return {
        "data_root": str(active),
        "data_root_explicit": config.DATA_ROOT_EXPLICIT,
        "database_path": str(database),
        "database_size": size,
        "has_database": has_database(active),
        "legacy_roots_with_data": [str(path) for path in candidate_roots()],
        "autofind_enabled": bool(
            config.DATA_ROOT_AUTOFIND and not _database_url_overridden()
        ),
        "adopted_from": (adoption or {}).get("source", "") or "",
        "adoption": adoption,
        "warnings": warnings,
    }
