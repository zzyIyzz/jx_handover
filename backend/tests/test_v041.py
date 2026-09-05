"""V0.4.1 production-hardening backup, replication and restore tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

from sqlalchemy import create_engine

from app import config
from app.services import backup as backup_service
from server_recovery import import_backup_bundle, schedule_imported_restore


class BackupEnvironment:
    def __init__(self, root: Path):
        self.root = root
        self.data_root = root / "server-data"
        self.database = self.data_root / "data" / "handover.db"
        self.imports = self.data_root / "imports"
        self.generated = self.data_root / "generated"
        self.snapshots = self.data_root / "snapshots"
        self.nas = root / "nas"
        self.database.parent.mkdir(parents=True)
        self.imports.mkdir(parents=True)
        self.generated.mkdir(parents=True)
        self.snapshots.mkdir(parents=True)
        connection = sqlite3.connect(str(self.database))
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO facts(value) VALUES (?)", [("第一条",), ("第二条",)]
        )
        connection.execute(
            "CREATE TABLE document_snapshots (id TEXT PRIMARY KEY, docx_path TEXT)"
        )
        connection.commit()
        connection.close()
        self.import_file = self.imports / "2026-08" / "脱敏工作日志.xlsx"
        self.import_file.parent.mkdir(parents=True)
        self.import_file.write_bytes(b"sanitized-work-log-v1")
        self.word_file = self.generated / "TEST" / "202608" / "交接班_V001.docx"
        self.word_file.parent.mkdir(parents=True)
        self.word_file.write_bytes(b"generated-word-v1")
        connection = sqlite3.connect(str(self.database))
        connection.execute(
            "INSERT INTO document_snapshots VALUES (?, ?)",
            ("snap_1", str(self.word_file)),
        )
        connection.commit()
        connection.close()
        self.engine = create_engine(
            f"sqlite:///{self.database.as_posix()}",
            connect_args={"timeout": 30},
        )

    def patches(self, *, nas: str = ""):
        return (
            mock.patch.multiple(
                config,
                USER_DATA_ROOT=self.data_root,
                DATA_DIR=self.data_root / "data",
                DATABASE_PATH=self.database,
                IMPORT_DIR=self.imports,
                GENERATED_DIR=self.generated,
                SNAPSHOT_DIR=self.snapshots,
                NAS_BACKUP_DIR=nas,
                PUBLIC_HOST="jx-handover.test",
            ),
            mock.patch.object(backup_service, "engine", self.engine),
        )

    def close(self):
        self.engine.dispose()


class V041FullBackupTest(unittest.TestCase):
    def test_full_backup_contains_database_imports_generated_and_sanitized_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = BackupEnvironment(Path(tmp))
            config_patch, engine_patch = environment.patches()
            try:
                with config_patch, engine_patch:
                    result = backup_service.create_full_backup(
                        reason="test", replicate=False
                    )
                    verified = backup_service.verify_full_backup(result["backup_id"])
            finally:
                environment.close()

            bundle = Path(result["local_path"])
            self.assertTrue(bundle.is_file())
            self.assertEqual(
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                result["bundle_sha256"],
            )
            self.assertEqual(verified["verification"], "verified")
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn("data/handover.db", names)
                self.assertIn("imports/2026-08/脱敏工作日志.xlsx", names)
                self.assertIn("generated/TEST/202608/交接班_V001.docx", names)
                self.assertIn("metadata/sanitized-config.json", names)
                sanitized = json.loads(
                    archive.read("metadata/sanitized-config.json").decode("utf-8")
                )
                self.assertFalse(sanitized["secrets_included"])
                self.assertNotIn("api_key", sanitized)
                database_copy = Path(tmp) / "verified.db"
                database_copy.write_bytes(archive.read("data/handover.db"))
            connection = sqlite3.connect(str(database_copy))
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                values = [
                    row[0]
                    for row in connection.execute("SELECT value FROM facts ORDER BY id")
                ]
            finally:
                connection.close()
            self.assertEqual(values, ["第一条", "第二条"])

    def test_full_backup_uses_short_workspace_for_long_chinese_business_paths(self):
        """A valid source path must not become invalid only while being backed up."""
        with tempfile.TemporaryDirectory() as tmp:
            long_root = (
                Path(tmp)
                / "JXV041-Final-Packaged-独立冒烟-20260901-090846"
                / "正式数据 本地固定盘"
            )
            environment = BackupEnvironment(long_root)
            filename = (
                "修水眉毛山风电场交接班记录_"
                "20260814-20260823_V001_长文本验收.docx"
            )
            base = environment.generated / "XS_MMS" / "202608" / "发布历史"
            # Keep the real source comfortably below MAX_PATH while making the
            # former data-root/snapshots/full_backups/.staging-* copy exceed it.
            padding = max(8, 225 - len(str(base / filename)))
            long_file = base / ("验" * padding) / filename
            long_file.parent.mkdir(parents=True)
            long_file.write_bytes(b"long-path-generated-word")
            relative = long_file.relative_to(environment.generated)
            former_staging_target = (
                environment.snapshots
                / "full_backups"
                / ".staging-20260901-091234-123456-abcdef"
                / "generated"
                / relative
            )
            self.assertLess(len(str(long_file)), 260)
            self.assertGreater(len(str(former_staging_target)), 260)

            config_patch, engine_patch = environment.patches()
            try:
                with config_patch, engine_patch:
                    result = backup_service.create_full_backup(
                        reason="long-path-regression", replicate=False
                    )
                    backup_service.verify_full_backup(result["backup_id"])
            finally:
                environment.close()

            with zipfile.ZipFile(result["local_path"]) as archive:
                archive_name = (Path("generated") / relative).as_posix()
                self.assertIn(archive_name, archive.namelist())
                self.assertEqual(
                    archive.read(archive_name), b"long-path-generated-word"
                )

    def test_nas_failure_remains_pending_and_later_retry_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = BackupEnvironment(root)
            blocked_nas = root / "nas-is-a-file"
            blocked_nas.write_text("not a directory", encoding="utf-8")
            config_patch, engine_patch = environment.patches(nas=str(blocked_nas))
            try:
                with config_patch, engine_patch:
                    result = backup_service.create_full_backup(reason="test")
                    self.assertEqual(result["nas_state"], "pending")
                    self.assertEqual(result["nas_attempts"], 1)
                    self.assertTrue(Path(result["local_path"]).is_file())

                config_patch, engine_patch = environment.patches(nas=str(environment.nas))
                with config_patch, engine_patch:
                    retry = backup_service.replicate_pending_backups(limit=10)
                    refreshed = backup_service.list_full_backups()[0]
            finally:
                environment.close()

            self.assertEqual(retry, {"attempted": 1, "synced": 1, "failed": 0})
            self.assertEqual(refreshed["nas_state"], "synced")
            self.assertEqual(refreshed["nas_attempts"], 2)
            nas_bundle = Path(refreshed["nas_path"])
            self.assertTrue(nas_bundle.is_file())
            self.assertEqual(
                hashlib.sha256(nas_bundle.read_bytes()).hexdigest(),
                refreshed["bundle_sha256"],
            )
            self.assertTrue(nas_bundle.with_suffix(".json").is_file())

    def test_restart_restore_keeps_pre_restore_backup_and_replaces_business_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = BackupEnvironment(Path(tmp))
            config_patch, engine_patch = environment.patches()
            try:
                with config_patch, engine_patch:
                    original = backup_service.create_full_backup(
                        reason="restore-source", replicate=False
                    )

                    connection = sqlite3.connect(str(environment.database))
                    connection.execute("DELETE FROM facts")
                    connection.execute("INSERT INTO facts(value) VALUES ('恢复前的新数据')")
                    connection.commit()
                    connection.close()
                    environment.import_file.write_bytes(b"changed-import")
                    environment.word_file.write_bytes(b"changed-word")
                    extra = environment.generated / "only-before-restore.docx"
                    extra.write_bytes(b"remove-me")

                    request = backup_service.schedule_restore(
                        original["backup_id"], requested_by="测试管理员"
                    )
                    self.assertEqual(request["state"], "pending_restart")
                    restored = backup_service.apply_pending_restore()
                    backups = backup_service.list_full_backups()
                    pre_restore = next(
                        item
                        for item in backups
                        if item.get("backup_id") == restored["pre_restore_backup_id"]
                    )
                    backup_service.verify_full_backup(pre_restore["backup_id"])
                    pending_after_restore = backup_service.pending_restore_status()
                    last_restore = backup_service.last_restore_result()
            finally:
                environment.close()

            connection = sqlite3.connect(str(environment.database))
            try:
                values = [
                    row[0]
                    for row in connection.execute("SELECT value FROM facts ORDER BY id")
                ]
                stored_word = Path(
                    connection.execute(
                        "SELECT docx_path FROM document_snapshots WHERE id='snap_1'"
                    ).fetchone()[0]
                )
            finally:
                connection.close()
            self.assertEqual(values, ["第一条", "第二条"])
            self.assertEqual(environment.import_file.read_bytes(), b"sanitized-work-log-v1")
            self.assertEqual(environment.word_file.read_bytes(), b"generated-word-v1")
            self.assertFalse(extra.exists())
            self.assertEqual(stored_word, environment.word_file)
            self.assertEqual(pre_restore["reason"], "pre-restore")
            self.assertIsNone(pending_after_restore)
            self.assertEqual(last_restore["state"], "completed")

    def test_empty_replacement_server_can_import_nas_bundle_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = BackupEnvironment(root / "source")
            source_patch, source_engine_patch = source.patches()
            try:
                with source_patch, source_engine_patch:
                    backup = backup_service.create_full_backup(
                        reason="disaster-source", replicate=False
                    )
            finally:
                source.close()

            nas = root / "nas"
            nas.mkdir()
            nas_bundle = nas / Path(backup["local_path"]).name
            nas_manifest = nas_bundle.with_suffix(".json")
            shutil.copy2(backup["local_path"], nas_bundle)
            shutil.copy2(backup["manifest_path"], nas_manifest)

            replacement = root / "replacement-server-data"
            imported = import_backup_bundle(nas_bundle, replacement)
            request = schedule_imported_restore(
                replacement, imported, requested_by="灾备测试管理员"
            )
            replacement_database = replacement / "data" / "handover.db"
            replacement_engine = create_engine(
                f"sqlite:///{replacement_database.as_posix()}",
                connect_args={"timeout": 30},
            )
            try:
                with mock.patch.multiple(
                    config,
                    USER_DATA_ROOT=replacement,
                    DATA_DIR=replacement / "data",
                    DATABASE_PATH=replacement_database,
                    IMPORT_DIR=replacement / "imports",
                    GENERATED_DIR=replacement / "generated",
                    SNAPSHOT_DIR=replacement / "snapshots",
                    NAS_BACKUP_DIR="",
                    PUBLIC_HOST="replacement.test",
                ), mock.patch.object(backup_service, "engine", replacement_engine):
                    restored = backup_service.apply_pending_restore()
            finally:
                replacement_engine.dispose()

            self.assertEqual(request["source"], "offline-nas-import")
            self.assertIsNone(restored["pre_restore_backup_id"])
            connection = sqlite3.connect(str(replacement_database))
            try:
                values = [
                    row[0]
                    for row in connection.execute("SELECT value FROM facts ORDER BY id")
                ]
            finally:
                connection.close()
            self.assertEqual(values, ["第一条", "第二条"])
            self.assertEqual(
                (replacement / "imports" / "2026-08" / "脱敏工作日志.xlsx").read_bytes(),
                b"sanitized-work-log-v1",
            )
            self.assertTrue(
                (replacement / "generated" / "TEST" / "202608" / "交接班_V001.docx").is_file()
            )


if __name__ == "__main__":
    unittest.main()
