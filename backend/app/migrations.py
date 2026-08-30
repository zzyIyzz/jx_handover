"""SQLite schema migration and safety backup for v0.3.0.

The application deliberately keeps migration logic small and idempotent.  It
never rewrites document snapshots; only missing columns/tables are added.
"""
from __future__ import annotations

import shutil
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


def _backup_database(target_engine: Engine, backup_dir: Path) -> Path | None:
    database_path = _sqlite_path(target_engine)
    if database_path is None or not database_path.exists() or database_path.stat().st_size == 0:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"handover_before_v0.3.0_{stamp}.db"
    counter = 1
    while target.exists():
        target = backup_dir / f"handover_before_v0.3.0_{stamp}_{counter}.db"
        counter += 1
    shutil.copy2(database_path, target)
    return target


def migrate_database(
    target_engine: Engine = engine,
    *,
    backup_dir: Path | None = None,
) -> dict:
    """Apply all known migrations and return a diagnostic summary."""

    backup = None
    changed: list[str] = []
    if _needs_v030_migration(target_engine):
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

    # SQLAlchemy creates only missing tables/indexes and leaves historical rows,
    # document snapshots and generated Word files untouched.
    Base.metadata.create_all(target_engine)
    return {
        "changed": changed,
        "backup_path": str(backup) if backup else None,
    }


def initialize_database() -> dict:
    return migrate_database(engine)
