"""Direct uvicorn starts must restore before migrations or accepting requests."""
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main
from app.services import backup
from test_v041 import BackupEnvironment


def startup_mocks(stack):
    events = []
    stack.enter_context(mock.patch.object(main.config, "validate_runtime_configuration"))
    stack.enter_context(mock.patch.object(main.config, "APP_MODE", "local"))
    stack.enter_context(mock.patch.object(main.data_root_service, "adopt_legacy_data_root", return_value={}))
    bootstrap = stack.enter_context(mock.patch.object(main, "initialize_application_data", side_effect=lambda: events.append("bootstrap")))
    stack.enter_context(mock.patch.object(main, "initialize_session_secret"))
    stack.enter_context(mock.patch.object(main, "SessionLocal"))
    stack.enter_context(mock.patch.object(main, "count_administrators", return_value=1))
    return events, bootstrap


def test_direct_startup_restores_before_bootstrap(tmp_path):
    environment = BackupEnvironment(tmp_path)
    try:
        with ExitStack() as stack:
            for patch in environment.patches():
                stack.enter_context(patch)
            original = backup.create_full_backup(reason="test", replicate=False)
            with closing(sqlite3.connect(str(environment.database))) as connection:
                connection.execute("DELETE FROM facts")
                connection.execute("INSERT INTO facts(value) VALUES ('newer')")
                connection.commit()
            backup.schedule_restore(original["backup_id"], requested_by="test")
            events, bootstrap = startup_mocks(stack)
            def assert_restored():
                with closing(sqlite3.connect(str(environment.database))) as connection:
                    assert connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 2
                assert backup.pending_restore_status() is None
                events.append("bootstrap")
            bootstrap.side_effect = assert_restored
            main.startup()
            assert events == ["bootstrap"]
            assert backup.last_restore_result()["state"] == "completed"
            main.startup()
            assert events == ["bootstrap", "bootstrap"]
    finally:
        environment.close()


def test_unsafe_restore_failure_aborts_startup_before_database_bootstrap():
    with ExitStack() as stack:
        events, bootstrap = startup_mocks(stack)
        stack.enter_context(mock.patch.object(main, "apply_pending_restore", side_effect=RuntimeError("restore blocked")))
        try:
            main.startup()
        except RuntimeError as exc:
            assert "restore blocked" in str(exc)
        else:
            raise AssertionError("unsafe restore failure must stop startup")
        bootstrap.assert_not_called()
        assert not events
