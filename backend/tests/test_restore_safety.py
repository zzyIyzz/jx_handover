"""Failure-injection coverage for fail-closed restore and retention."""
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import config
from app.services import backup
from test_v041 import BackupEnvironment


@pytest.fixture
def env(tmp_path):
    environment = BackupEnvironment(tmp_path)
    with ExitStack() as stack:
        for patch in environment.patches():
            stack.enter_context(patch)
        yield environment
    environment.close()


def schedule(env):
    source = backup.create_full_backup(reason="test", replicate=False)
    backup.schedule_restore(source["backup_id"], requested_by="test")
    env.import_file.write_bytes(b"new-live-import")
    return source


def assert_safe_failure(env):
    assert backup.last_restore_result()["state"] == "failed"
    assert backup.pending_restore_status() is None
    assert not (backup._restore_root() / "applying.json").exists()
    assert env.import_file.read_bytes() == b"new-live-import"
    assert backup.apply_pending_restore() is None


def test_changed_scheduled_digest_fails_before_backup_or_moves(env):
    schedule(env)
    marker = backup._restore_root() / "pending.json"
    request = backup._read_manifest(marker)
    request["bundle_sha256"] = "0" * 64
    backup._write_json_atomic(marker, request)
    with mock.patch.object(backup, "create_full_backup") as create:
        with pytest.raises(RuntimeError, match="SHA256"):
            backup.apply_pending_restore()
        create.assert_not_called()
    assert_safe_failure(env)


def test_target_root_mismatch_fails_without_touching_live_data(env):
    schedule(env)
    marker = backup._restore_root() / "pending.json"
    request = backup._read_manifest(marker)
    request["target_data_root"] = str(env.root / "wrong-root")
    backup._write_json_atomic(marker, request)
    with pytest.raises(RuntimeError, match="目录"):
        backup.apply_pending_restore()
    assert_safe_failure(env)


def test_corrupt_pending_records_failure_and_does_not_repeat(env):
    schedule(env)
    (backup._restore_root() / "pending.json").write_text("broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="清单"):
        backup.apply_pending_restore()
    assert_safe_failure(env)


def test_pre_restore_backup_failure_is_recorded(env):
    schedule(env)
    with mock.patch.object(backup, "create_full_backup", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            backup.apply_pending_restore()
    assert_safe_failure(env)


def test_post_install_failure_restores_original_database_and_files(env):
    schedule(env)
    with closing(sqlite3.connect(env.database)) as connection, connection:
        connection.execute("INSERT INTO facts(value) VALUES ('latest live')")
    with mock.patch.object(backup, "_rewrite_snapshot_paths", side_effect=RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="injected"):
            backup.apply_pending_restore()
    assert_safe_failure(env)
    with closing(sqlite3.connect(env.database)) as connection:
        assert connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 3


def test_interrupted_restore_preserves_evidence_and_blocks_replay(env):
    schedule(env)
    with mock.patch.object(backup, "_rewrite_snapshot_paths", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            backup.apply_pending_restore()
    applying = backup._read_manifest(backup._restore_root() / "applying.json")
    assert Path(applying["rollback_directory"]).is_dir()
    assert Path(applying["staging_directory"]).is_dir()
    with mock.patch.object(backup, "verify_full_backup") as verify:
        with pytest.raises(RuntimeError, match="阻止"):
            backup.apply_pending_restore()
        verify.assert_not_called()


def test_applying_marker_blocks_even_when_pending_missing(env):
    restore = backup._restore_root()
    backup._write_json_atomic(restore / "applying.json", {"state": "applying"})
    with pytest.raises(RuntimeError, match="阻止"):
        backup.apply_pending_restore()


def test_rollback_failure_preserves_guard_and_reports_error(env):
    schedule(env)
    real_replace = backup.os.replace

    def replace(source, target):
        if Path(source).parent.name.startswith("b-"):
            raise OSError("rollback denied")
        return real_replace(source, target)

    with mock.patch.object(backup.os, "replace", side_effect=replace):
        with mock.patch.object(backup, "_rewrite_snapshot_paths", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError, match="自动回滚失败"):
                backup.apply_pending_restore()
    result = backup.last_restore_result()
    assert result["state"] == "rollback_failed"
    assert result["rollback_errors"] == ["rollback denied"]
    assert Path(result["rollback_directory"]).is_dir()
    assert Path(result["staging_directory"]).is_dir()
    assert backup.pending_restore_status() is not None
    with pytest.raises(RuntimeError, match="阻止"):
        backup.apply_pending_restore()


def test_prune_preserves_pending_selection_and_last_safety_copy(env):
    selected = schedule(env)
    safety = backup.create_full_backup(reason="pre-restore", replicate=False)
    disposable = backup.create_full_backup(reason="manual", replicate=False)
    backup._write_json_atomic(backup._restore_root() / "last-result.json", {
        "state": "completed", "pre_restore_backup_id": safety["backup_id"],
    })
    with mock.patch.object(config, "BACKUP_KEEP_MANUAL", 0):
        result = backup.prune_full_backups()
    assert Path(selected["local_path"]).is_file()
    assert Path(safety["local_path"]).is_file()
    assert disposable["backup_id"] in result["removed"]


def test_prune_does_nothing_with_unreadable_pending(env):
    source = schedule(env)
    (backup._restore_root() / "pending.json").write_text("broken", encoding="utf-8")
    with mock.patch.object(config, "BACKUP_KEEP_MANUAL", 0):
        result = backup.prune_full_backups()
    assert not result["removed"]
    assert Path(source["local_path"]).is_file()


def test_restore_invalidates_sessions_from_old_snapshot(env):
    with closing(sqlite3.connect(env.database)) as connection, connection:
        connection.execute("CREATE TABLE staff (id INTEGER, session_version INTEGER)")
        connection.execute("INSERT INTO staff VALUES (1, 7)")
    schedule(env)
    result = backup.apply_pending_restore()
    assert result["state"] == "completed"
    assert result["invalidated_sessions"] == 1
    with closing(sqlite3.connect(env.database)) as connection:
        version = connection.execute("SELECT session_version FROM staff").fetchone()[0]
    assert version != 7
    assert 0 < version < 2 ** 52
