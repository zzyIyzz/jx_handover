"""SQLite schema migrations with an integrity-checked safety backup.

The application deliberately keeps migration logic small and idempotent.  It
never rewrites document snapshots; only missing columns/tables are added.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app import config
from app.db import Base, engine
import app.models  # noqa: F401 - register every model on Base.metadata


def _sqlite_path(target_engine: Engine) -> Path | None:
    database = target_engine.url.database
    if target_engine.url.get_backend_name() != "sqlite" or not database:
        return None
    if database == ":memory:":
        return None
    return Path(database).resolve()


def _needs_v030_migration(target_engine: Engine) -> bool:
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return False
    if "handover_items" in tables:
        columns = {column["name"] for column in inspector.get_columns("handover_items")}
        if not {"section", "completed_by", "sort_order"}.issubset(columns):
            return True
    if "import_jobs" in tables:
        columns = {column["name"] for column in inspector.get_columns("import_jobs")}
        if not {"stored_path", "parser_key"}.issubset(columns):
            return True
    if "monthly_plan_items" in tables:
        columns = {column["name"] for column in inspector.get_columns("monthly_plan_items")}
        if "library_id" not in columns:
            return True
    return not {"external_assessments", "section_import_previews"}.issubset(tables)


def _needs_v040_migration(target_engine: Engine) -> bool:
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return False
    if "section_import_previews" in tables:
        columns = {
            column["name"]
            for column in inspector.get_columns("section_import_previews")
        }
        if not {"ai_status", "ai_model", "ai_usage_json"}.issubset(columns):
            return True
    return "audit_events" not in tables


def _needs_account_migration(target_engine: Engine) -> bool:
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    if not tables or "staff" not in tables:
        return False
    columns = {column["name"] for column in inspector.get_columns("staff")}
    return not {
        "password_hash",
        "must_change_password",
        "session_version",
        "password_updated_at",
        "last_login_at",
    }.issubset(columns)


def _backup_database(target_engine: Engine, backup_dir: Path) -> Path | None:
    database_path = _sqlite_path(target_engine)
    if database_path is None or not database_path.exists() or database_path.stat().st_size == 0:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"handover_before_migration_{stamp}.db"
    counter = 1
    while target.exists():
        target = backup_dir / f"handover_before_migration_{stamp}_{counter}.db"
        counter += 1
    temporary = target.with_suffix(".db.tmp")
    source_connection = sqlite3.connect(str(database_path), timeout=30)
    target_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        # SQLite's online backup API includes committed WAL pages and produces
        # one self-contained file even after an unclean previous shutdown.
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    check_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        check = check_connection.execute("PRAGMA quick_check").fetchone()
    finally:
        check_connection.close()
    if not check or str(check[0]).lower() != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("迁移前数据库备份完整性检查未通过。")
    temporary.replace(target)
    return target


def migrate_database(
    target_engine: Engine = engine,
    *,
    backup_dir: Path | None = None,
) -> dict:
    """Apply all known migrations and return a diagnostic summary."""

    backup = None
    changed: list[str] = []
    if (_needs_v030_migration(target_engine)
            or _needs_v040_migration(target_engine)
            or _needs_account_migration(target_engine)):
        backup = _backup_database(
            target_engine,
            backup_dir or (config.SNAPSHOT_DIR / "database_backups"),
        )

    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    with target_engine.begin() as connection:
        if "handover_items" in tables:
            columns = {column["name"] for column in inspector.get_columns("handover_items")}
            added_section = False
            added_sort_order = False
            if "section" not in columns:
                connection.execute(text(
                    "ALTER TABLE handover_items "
                    "ADD COLUMN section TEXT NOT NULL DEFAULT 'handover'"
                ))
                changed.append("handover_items.section")
                added_section = True
            if "completed_by" not in columns:
                connection.execute(text(
                    "ALTER TABLE handover_items "
                    "ADD COLUMN completed_by TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("handover_items.completed_by")
            if "sort_order" not in columns:
                connection.execute(text(
                    "ALTER TABLE handover_items "
                    "ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
                ))
                changed.append("handover_items.sort_order")
                added_sort_order = True
            # Old versions computed the section at response time.  Persist the
            # status-based suggestion now without inventing a completed person.
            if added_section:
                connection.execute(text(
                    "UPDATE handover_items SET section = CASE "
                    "WHEN status = 'completed' THEN 'important' ELSE 'handover' END"
                ))
            if added_sort_order:
                connection.execute(text(
                    "UPDATE handover_items SET sort_order = rowid"
                ))

        if "import_jobs" in tables:
            columns = {column["name"] for column in inspector.get_columns("import_jobs")}
            if "stored_path" not in columns:
                connection.execute(text(
                    "ALTER TABLE import_jobs "
                    "ADD COLUMN stored_path TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("import_jobs.stored_path")
            if "parser_key" not in columns:
                connection.execute(text(
                    "ALTER TABLE import_jobs "
                    "ADD COLUMN parser_key TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("import_jobs.parser_key")

        if "monthly_plan_items" in tables:
            columns = {column["name"] for column in inspector.get_columns("monthly_plan_items")}
            if "library_id" not in columns:
                connection.execute(text(
                    "ALTER TABLE monthly_plan_items "
                    "ADD COLUMN library_id TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("monthly_plan_items.library_id")

        if "section_import_previews" in tables:
            columns = {
                column["name"]
                for column in inspector.get_columns("section_import_previews")
            }
            if "ai_status" not in columns:
                connection.execute(text(
                    "ALTER TABLE section_import_previews "
                    "ADD COLUMN ai_status TEXT NOT NULL DEFAULT 'not_requested'"
                ))
                changed.append("section_import_previews.ai_status")
            if "ai_model" not in columns:
                connection.execute(text(
                    "ALTER TABLE section_import_previews "
                    "ADD COLUMN ai_model TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("section_import_previews.ai_model")
            if "ai_usage_json" not in columns:
                connection.execute(text(
                    "ALTER TABLE section_import_previews "
                    "ADD COLUMN ai_usage_json TEXT NOT NULL DEFAULT '{}'"
                ))
                changed.append("section_import_previews.ai_usage_json")

        if "staff" in tables:
            columns = {column["name"] for column in inspector.get_columns("staff")}
            if "password_hash" not in columns:
                connection.execute(text(
                    "ALTER TABLE staff "
                    "ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"
                ))
                changed.append("staff.password_hash")
            if "must_change_password" not in columns:
                connection.execute(text(
                    "ALTER TABLE staff "
                    "ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1"
                ))
                changed.append("staff.must_change_password")
            if "session_version" not in columns:
                connection.execute(text(
                    "ALTER TABLE staff "
                    "ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1"
                ))
                changed.append("staff.session_version")
            if "password_updated_at" not in columns:
                connection.execute(text(
                    "ALTER TABLE staff ADD COLUMN password_updated_at TEXT"
                ))
                changed.append("staff.password_updated_at")
            if "last_login_at" not in columns:
                connection.execute(text(
                    "ALTER TABLE staff ADD COLUMN last_login_at TEXT"
                ))
                changed.append("staff.last_login_at")

    # SQLAlchemy creates only missing tables/indexes and leaves historical rows,
    # document snapshots and generated Word files untouched.
    Base.metadata.create_all(target_engine)
    return {
        "changed": changed,
        "backup_path": str(backup) if backup else None,
    }


def initialize_database() -> dict:
    return migrate_database(engine)
