"""Administrator persistence, offline recovery and the fixed live-data directory.

These three regressions all looked like "the system lost something" from the
operator's seat: the management menu disappeared, AI stopped organising work
logs, and a redeploy appeared to wipe every account.  Each test below pins the
behaviour that keeps them from coming back.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app import config
from app.api import admin as admin_api
from app.api import session as session_api
from app.db import Base, get_db
from app.models import Staff
from app.security import (
    account_role,
    count_administrators,
    hash_password,
    is_administrator,
    reconcile_administrators,
)
from app.services import data_root as data_root_service


PASSWORD = "Admin-personal-2026!"
BASE_SETTINGS = {
    "APP_MODE": "cloud",
    "AUTH_REQUIRED": True,
    "ACCOUNT_LOGIN_ENABLED": True,
    "INITIAL_ACCOUNT_PASSWORD": PASSWORD,
    "ACCESS_CODE": "",
    "COOKIE_SECURE": True,
    "SESSION_SECRET": "admin-persistence-test-secret-2026-long",
    "SESSION_TTL_HOURS": 12,
}


class AdministratorPersistenceTest(unittest.TestCase):
    """The database owns the administrator flag; the environment only promotes."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.app = FastAPI()
        self.app.include_router(session_api.router)
        self.app.include_router(admin_api.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.app.dependency_overrides[get_db] = override_db

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed(self, names: list[str], *, admin_flags: dict[str, int] | None = None) -> dict[str, int]:
        flags = admin_flags or {}
        db = self.Session()
        ids: dict[str, int] = {}
        try:
            for name in names:
                staff = Staff(
                    station_code="REGION",
                    name=name,
                    role="",
                    note="",
                    password_hash=hash_password(PASSWORD),
                    must_change_password=0,
                    session_version=1,
                    is_admin=int(flags.get(name, 0)),
                )
                db.add(staff)
                db.flush()
                ids[name] = staff.id
            db.commit()
        finally:
            db.close()
        return ids

    def _login(self, client: TestClient, name: str) -> str:
        response = client.post("/api/session/login", json={"name": name, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["session_token"]
        client.headers["Authorization"] = "Bearer " + token
        return token

    def test_administrator_right_survives_losing_the_environment_variable(self):
        self._seed(["甲管理员", "乙操作员"], admin_flags={"甲管理员": 1})
        db = self.Session()
        try:
            # A redeploy that drops JX_ADMIN_NAMES must not demote anybody.
            with mock.patch.object(config, "ADMIN_NAMES", set()):
                self.assertEqual(count_administrators(db), 1)
                staff = db.query(Staff).filter(Staff.name == "甲管理员").one()
                self.assertTrue(is_administrator(staff))
                self.assertEqual(account_role(staff), "admin")
                operator = db.query(Staff).filter(Staff.name == "乙操作员").one()
                self.assertEqual(account_role(operator), "operator")
        finally:
            db.close()

    def test_configured_names_are_promoted_on_every_startup(self):
        self._seed(["甲管理员", "乙操作员"])
        db = self.Session()
        try:
            with mock.patch.object(config, "ADMIN_NAMES", {"甲管理员"}):
                result = reconcile_administrators(db)
            self.assertEqual(result["promoted"], ["甲管理员"])
            self.assertEqual(result["recovered"], [])
            self.assertEqual(result["administrators"], ["甲管理员"])
            # Idempotent: a second start changes nothing.
            with mock.patch.object(config, "ADMIN_NAMES", {"甲管理员"}):
                again = reconcile_administrators(db)
            self.assertEqual(again["promoted"], [])
            self.assertEqual(again["administrators"], ["甲管理员"])
        finally:
            db.close()

    def test_recovery_name_takes_over_only_when_nobody_is_administrator(self):
        self._seed(["片区负责人", "乙操作员"])
        db = self.Session()
        try:
            with mock.patch.object(config, "ADMIN_NAMES", set()), \
                    mock.patch.object(config, "DEFAULT_ADMIN_NAMES", {"片区负责人"}):
                result = reconcile_administrators(db)
            self.assertEqual(result["recovered"], ["片区负责人"])
            self.assertEqual(result["administrators"], ["片区负责人"])
        finally:
            db.close()

    def test_missing_administrator_is_reported_instead_of_failing_silently(self):
        self._seed(["乙操作员"])
        db = self.Session()
        try:
            with mock.patch.object(config, "ADMIN_NAMES", set()), \
                    mock.patch.object(config, "DEFAULT_ADMIN_NAMES", set()):
                result = reconcile_administrators(db)
            self.assertEqual(result["administrators"], [])
            self.assertEqual(count_administrators(db), 0)
        finally:
            db.close()

    def test_grant_and_revoke_through_the_management_api(self):
        ids = self._seed(["甲管理员", "乙操作员", "丙操作员"], admin_flags={"甲管理员": 1})
        with mock.patch.multiple(config, ADMIN_NAMES=set(), DEFAULT_ADMIN_NAMES=set(), **BASE_SETTINGS):
            with TestClient(self.app, base_url="https://handover.example.test:1215") as client:
                self._login(client, "甲管理员")
                accounts = client.get("/api/admin/accounts").json()
                by_name = {row["name"]: row for row in accounts}
                self.assertTrue(by_name["甲管理员"]["is_admin"])
                self.assertFalse(by_name["乙操作员"]["is_admin"])
                self.assertFalse(by_name["乙操作员"]["admin_from_env"])

                granted = client.patch(
                    f"/api/admin/accounts/{ids['乙操作员']}", json={"is_admin": True}
                )
                self.assertEqual(granted.status_code, 200, granted.text)
                self.assertEqual(granted.json()["account_role"], "admin")

                revoked = client.patch(
                    f"/api/admin/accounts/{ids['乙操作员']}", json={"is_admin": False}
                )
                self.assertEqual(revoked.status_code, 200, revoked.text)
                self.assertEqual(revoked.json()["account_role"], "operator")

    def test_last_administrator_can_never_be_removed_or_disabled(self):
        # Shared-identity mode acts as "本机用户", so the self-guard does not
        # fire and the last-administrator guard is the only thing standing
        # between one click and an unmanageable system.
        ids = self._seed(["甲管理员", "乙操作员"], admin_flags={"甲管理员": 1})
        shared = {
            **BASE_SETTINGS,
            "APP_MODE": "server",
            "AUTH_REQUIRED": False,
            "ACCOUNT_LOGIN_ENABLED": False,
            "COOKIE_SECURE": False,
        }
        with mock.patch.multiple(config, ADMIN_NAMES=set(), DEFAULT_ADMIN_NAMES=set(), **shared):
            with TestClient(self.app) as client:
                self.assertEqual(client.get("/api/admin/accounts").status_code, 200)

                demote = client.patch(
                    f"/api/admin/accounts/{ids['甲管理员']}", json={"is_admin": False}
                )
                self.assertEqual(demote.status_code, 409)
                self.assertIn("至少一个管理员", demote.json()["detail"])

                disable = client.patch(
                    f"/api/admin/accounts/{ids['甲管理员']}", json={"is_active": False}
                )
                self.assertEqual(disable.status_code, 409)

                # Granting a second administrator first makes the same change safe.
                grant = client.patch(
                    f"/api/admin/accounts/{ids['乙操作员']}", json={"is_admin": True}
                )
                self.assertEqual(grant.status_code, 200, grant.text)
                retry = client.patch(
                    f"/api/admin/accounts/{ids['甲管理员']}", json={"is_admin": False}
                )
                self.assertEqual(retry.status_code, 200, retry.text)
                self.assertEqual(retry.json()["account_role"], "operator")

    def test_self_account_cannot_be_changed_from_the_management_page(self):
        ids = self._seed(["甲管理员", "乙管理员"], admin_flags={"甲管理员": 1, "乙管理员": 1})
        with mock.patch.multiple(config, ADMIN_NAMES=set(), DEFAULT_ADMIN_NAMES=set(), **BASE_SETTINGS):
            with TestClient(self.app, base_url="https://handover.example.test:1215") as client:
                self._login(client, "甲管理员")
                demote_self = client.patch(
                    f"/api/admin/accounts/{ids['甲管理员']}", json={"is_admin": False}
                )
                self.assertEqual(demote_self.status_code, 409)
                self.assertIn("自己的账号", demote_self.json()["detail"])

    def test_environment_pinned_administrator_cannot_be_demoted_from_the_ui(self):
        ids = self._seed(["环境管理员", "库管理员"], admin_flags={"库管理员": 1})
        with mock.patch.multiple(config, ADMIN_NAMES={"环境管理员"}, DEFAULT_ADMIN_NAMES=set(), **BASE_SETTINGS):
            with TestClient(self.app, base_url="https://handover.example.test:1215") as client:
                self._login(client, "库管理员")
                accounts = {row["name"]: row for row in client.get("/api/admin/accounts").json()}
                self.assertTrue(accounts["环境管理员"]["admin_from_env"])
                self.assertEqual(accounts["环境管理员"]["account_role"], "admin")
                self.assertFalse(accounts["库管理员"]["admin_from_env"])

                demote = client.patch(
                    f"/api/admin/accounts/{ids['环境管理员']}", json={"is_admin": False}
                )
                self.assertEqual(demote.status_code, 409)
                self.assertIn("JX_ADMIN_NAMES", demote.json()["detail"])

                disable = client.patch(
                    f"/api/admin/accounts/{ids['环境管理员']}", json={"is_active": False}
                )
                self.assertEqual(disable.status_code, 409)

                rename = client.patch(
                    f"/api/admin/accounts/{ids['环境管理员']}", json={"name": "别的名字"}
                )
                self.assertEqual(rename.status_code, 409)


class DataRootAdoptionTest(unittest.TestCase):
    """One fixed data directory, with a copy-only rescue for the old layout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.active = self.root / "runtime"
        self.legacy = self.root / "runtime-server"
        self.addCleanup(self._tmp.cleanup)

    def _make_database(self, root: Path, *, marker: str = "real", corrupt: bool = False) -> Path:
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        database = data_dir / "handover.db"
        if corrupt:
            database.write_bytes(b"this is definitely not a sqlite database" * 40)
            return database
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO staff (name) VALUES (?)", (marker,))
            connection.commit()
        finally:
            connection.close()
        (root / "generated").mkdir(parents=True, exist_ok=True)
        (root / "generated" / "old.docx").write_bytes(b"word-document")
        return database

    def _patched(self, *, candidates: tuple[Path, ...], autofind: bool = True):
        data_dir = self.active / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return mock.patch.multiple(
            config,
            USER_DATA_ROOT=self.active,
            DATA_DIR=data_dir,
            LEGACY_DATA_ROOT_CANDIDATES=candidates,
            DATA_ROOT_AUTOFIND=autofind,
        )

    def test_legacy_directory_is_copied_and_left_untouched(self):
        source_database = self._make_database(self.legacy, marker="老数据")
        with self._patched(candidates=(self.legacy,)), \
                mock.patch.dict(os.environ, {"JX_DATABASE_URL": ""}):
            report = data_root_service.adopt_legacy_data_root()
            self.assertEqual(report["state"], "adopted", report)
            self.assertEqual(report["database_check"], "ok")
            self.assertGreaterEqual(report["copied_files"], 2)

            adopted = data_root_service.database_path(self.active)
            self.assertTrue(adopted.is_file())
            connection = sqlite3.connect(adopted)
            try:
                rows = connection.execute("SELECT name FROM staff").fetchall()
            finally:
                connection.close()
            self.assertEqual(rows, [("老数据",)])
            # Never moved, never deleted: the original is still complete.
            self.assertTrue(source_database.is_file())
            self.assertTrue((self.legacy / "generated" / "old.docx").is_file())

            # The rescue leaves a note behind so the management page can say
            # where the data originally came from.
            recorded = data_root_service.read_adoption_report()
            self.assertIsNotNone(recorded)
            self.assertEqual(recorded["state"], "adopted")
            self.assertEqual(Path(recorded["source"]), self.legacy.resolve())

            fresh = data_root_service.data_root_report()
            self.assertEqual(fresh["adopted_from"], str(self.legacy.resolve()))

    def test_adoption_never_overwrites_a_directory_that_already_holds_data(self):
        self._make_database(self.active, marker="现用数据")
        self._make_database(self.legacy, marker="老数据")
        with self._patched(candidates=(self.legacy,)):
            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(report["state"], "skipped")
        connection = sqlite3.connect(data_root_service.database_path(self.active))
        try:
            rows = connection.execute("SELECT name FROM staff").fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("现用数据",)])

    def test_several_legacy_candidates_are_never_chosen_automatically(self):
        other = self.root / "backend-runtime"
        self._make_database(self.legacy, marker="甲")
        self._make_database(other, marker="乙")
        with self._patched(candidates=(self.legacy, other)):
            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(report["state"], "ambiguous")
        self.assertIn("data_location.py", report["message"])
        self.assertFalse(data_root_service.has_database(self.active))

        # An explicit choice is honoured.
        with self._patched(candidates=(self.legacy, other)):
            chosen = data_root_service.adopt_legacy_data_root(source=other)
        self.assertEqual(chosen["state"], "adopted")
        connection = sqlite3.connect(data_root_service.database_path(self.active))
        try:
            rows = connection.execute("SELECT name FROM staff").fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("乙",)])

    def test_a_damaged_database_is_rolled_back_instead_of_being_adopted(self):
        self._make_database(self.legacy, corrupt=True)
        with self._patched(candidates=(self.legacy,)):
            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["copied_files"], 0)
        self.assertFalse(data_root_service.database_path(self.active).exists())
        # The source is untouched, so a real recovery is still possible.
        self.assertTrue(data_root_service.database_path(self.legacy).is_file())

    def test_autofind_can_be_switched_off(self):
        self._make_database(self.legacy)
        with self._patched(candidates=(self.legacy,), autofind=False):
            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(report["state"], "disabled")
        self.assertFalse(data_root_service.has_database(self.active))

    def test_dry_run_prediction_matches_the_real_decision(self):
        # ``data_location.py --dry-run`` shares its decision function with the
        # real call.  If the two ever drift, an operator plans a rescue that the
        # service then refuses, or expects records that never arrive.
        self._make_database(self.active, marker="现用数据")
        self._make_database(self.legacy, marker="老数据")
        with self._patched(candidates=(self.legacy,)):
            plan = data_root_service.adoption_plan()
            report = data_root_service.adopt_legacy_data_root()
        self.assertTrue(plan["active_has_database"])
        self.assertEqual(plan["state"], "skipped")
        self.assertEqual(report["state"], plan["state"])
        self.assertEqual(report["message"], plan["message"])

    def test_plan_names_exactly_the_source_the_real_call_copies(self):
        self._make_database(self.legacy, marker="老数据")
        fresh = self.root / "fresh-root"
        with mock.patch.multiple(
            config,
            USER_DATA_ROOT=fresh,
            DATA_DIR=fresh / "data",
            LEGACY_DATA_ROOT_CANDIDATES=(self.legacy,),
            DATA_ROOT_AUTOFIND=True,
        ), mock.patch.dict(os.environ, {"JX_DATABASE_URL": ""}):
            plan = data_root_service.adoption_plan()
            self.assertEqual(plan["state"], "adopt")
            self.assertFalse(plan["active_has_database"])
            self.assertEqual(Path(plan["source"]), self.legacy.resolve())

            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(report["state"], "adopted")
        self.assertEqual(report["source"], plan["source"])

    def test_ambiguous_and_disabled_predictions_match_too(self):
        other = self.root / "backend-runtime"
        self._make_database(self.legacy, marker="甲")
        self._make_database(other, marker="乙")
        with self._patched(candidates=(self.legacy, other)):
            plan = data_root_service.adoption_plan()
            report = data_root_service.adopt_legacy_data_root()
        self.assertEqual(plan["state"], "ambiguous")
        self.assertEqual(report["state"], plan["state"])
        self.assertEqual(report["message"], plan["message"])

        with self._patched(candidates=(self.legacy, other), autofind=False):
            off = data_root_service.adoption_plan()
        self.assertEqual(off["state"], "disabled")
        self.assertIn("JX_DATA_ROOT_AUTOFIND", off["message"])

    def test_report_warns_about_a_left_behind_directory(self):
        self._make_database(self.active, marker="现用数据")
        self._make_database(self.legacy, marker="老数据")
        with self._patched(candidates=(self.legacy,)), \
                mock.patch.object(config, "DATA_ROOT_EXPLICIT", False):
            report = data_root_service.data_root_report()
        self.assertTrue(report["has_database"])
        self.assertFalse(report["data_root_explicit"])
        self.assertEqual(len(report["legacy_roots_with_data"]), 1)
        self.assertTrue(
            any("历史数据目录" in item for item in report["warnings"]), report["warnings"]
        )
        self.assertTrue(
            any("JX_HANDOVER_DATA_DIR" in item for item in report["warnings"]),
            report["warnings"],
        )


class AiAvailabilityTest(unittest.TestCase):
    """Turning AI off is a configuration state, never an import failure."""

    def test_disabled_preview_explains_itself(self):
        from app.services.importer.sections import _apply_ai_suggestions

        with mock.patch.object(config, "AI_MODE", "mock"), \
                mock.patch.object(config, "AI_MODE_REQUESTED", "mock"):
            result = _apply_ai_suggestions([], batch=mock.MagicMock(), station=mock.MagicMock())
        self.assertEqual(result["status"], "disabled")
        self.assertIn("AI_MODE=mock", result["reason"])

    def test_auto_mode_follows_the_api_key(self):
        with mock.patch.object(config, "AI_MODE_REQUESTED", "auto"), \
                mock.patch.object(config, "QWEN_API_KEY", ""):
            self.assertEqual(config._resolve_ai_mode(), "mock")
        with mock.patch.object(config, "AI_MODE_REQUESTED", "auto"), \
                mock.patch.object(config, "QWEN_API_KEY", "sk-test"):
            self.assertEqual(config._resolve_ai_mode(), "qwen")
        with mock.patch.object(config, "AI_MODE_REQUESTED", "qwen"), \
                mock.patch.object(config, "QWEN_API_KEY", ""):
            self.assertEqual(config._resolve_ai_mode(), "qwen")

    def test_base_url_and_model_have_working_defaults(self):
        self.assertTrue(config.QWEN_BASE_URL.startswith("https://"))
        self.assertTrue(config.QWEN_MODEL)


class OfflineAdministratorRecoveryTest(unittest.TestCase):
    """``manage_admin.py`` is the only way back in, so it must never make things worse.

    Run as a subprocess because the whole point is that it works from a console on
    a server whose schema and configuration are whatever the last deploy left behind.
    """

    SCRIPT = BACKEND_ROOT / "scripts" / "manage_admin.py"

    def _environment(self, data_root: Path, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        for name in list(env):
            if name.startswith(("JX_", "AI_", "QWEN_")):
                env.pop(name, None)
        env.update({
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONIOENCODING": "utf-8",
            "JX_HANDOVER_MODE": "server",
            "JX_HANDOVER_DATA_DIR": str(data_root),
            "JX_ADMIN_NAMES": "",
            "JX_DEFAULT_ADMIN_NAMES": "",
        })
        env.update(extra)
        return env

    def _run(self, data_root: Path, *arguments: str, **extra: str):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *arguments],
            cwd=PROJECT_ROOT,
            env=self._environment(data_root, **extra),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )

    def _write_old_schema(self, data_root: Path, names: list[str]) -> Path:
        """A database from before the flag existed - no ``is_admin`` column."""
        data_dir = data_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        database = data_dir / "handover.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE staff ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "station_code TEXT NOT NULL DEFAULT 'REGION', "
                "name TEXT NOT NULL, "
                "role TEXT NOT NULL DEFAULT '', "
                "note TEXT NOT NULL DEFAULT '', "
                "is_active INTEGER NOT NULL DEFAULT 1, "
                "password_hash TEXT NOT NULL DEFAULT '', "
                "must_change_password INTEGER NOT NULL DEFAULT 1, "
                "session_version INTEGER NOT NULL DEFAULT 1, "
                "password_updated_at TEXT, last_login_at TEXT, created_at TEXT)"
            )
            connection.executemany(
                "INSERT INTO staff (name) VALUES (?)", [(name,) for name in names]
            )
            connection.commit()
        finally:
            connection.close()
        return database

    def test_listing_works_on_a_database_from_before_the_flag_existed(self):
        # Right after pulling new code the service has not restarted yet, so the
        # schema is still the old one.  Listing used to raise
        # "no such column: staff.is_admin" - exactly when the operator most
        # needs to find out who can still manage the system.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_old_schema(root, ["甲管理员", "乙操作员"])
            completed = self._run(root, "--list", "--json")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["status"], "listed")
            self.assertEqual(payload["administrators"], [])

            # The run migrated the schema instead of merely surviving it.
            connection = sqlite3.connect(root / "data" / "handover.db")
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(staff)")
                }
            finally:
                connection.close()
            self.assertIn("is_admin", columns)

    def test_missing_database_is_refused_rather_than_created(self):
        # Creating an empty database here would be silent data loss: the startup
        # rescue only adopts a legacy directory while the active root has no
        # database, so a stray empty file would lock the real data out forever.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = self._run(root, "--list")
            self.assertEqual(completed.returncode, 2)
            self.assertIn("data_location.py", completed.stderr)
            # Importing the configuration still lays out empty folders, which is
            # harmless.  What must never appear is a database file, because that
            # is the only thing the startup rescue checks before adopting.
            self.assertEqual(list(root.rglob("*.db")), [])
            self.assertFalse(data_root_service.has_database(root))

    def test_grant_then_revoke_keeps_at_least_one_administrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_old_schema(root, ["甲管理员", "乙操作员"])

            granted = self._run(root, "--grant", "甲管理员")
            self.assertEqual(granted.returncode, 0, granted.stderr)
            listed = self._run(root, "--list", "--json")
            payload = json.loads(listed.stdout.strip().splitlines()[-1])
            self.assertEqual(
                [row["name"] for row in payload["administrators"]], ["甲管理员"]
            )
            self.assertFalse(payload["administrators"][0]["from_env"])

            # The recovery console obeys the same rule as the management page.
            refused = self._run(root, "--revoke", "甲管理员")
            self.assertEqual(refused.returncode, 2)
            self.assertIn("至少一个管理员", refused.stderr)

            unknown = self._run(root, "--revoke", "不存在的人")
            self.assertEqual(unknown.returncode, 2)
            self.assertIn("没有找到启用人员", unknown.stderr)

    def test_recovery_name_is_announced_when_nobody_is_in_charge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_old_schema(root, ["片区负责人", "乙操作员"])
            completed = self._run(
                root, "--list", "--json", JX_DEFAULT_ADMIN_NAMES="片区负责人"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["administrators"], [])
            self.assertEqual(payload["recovery_admin_names"], ["片区负责人"])

            # The console text has to say the same thing: an operator reading
            # "(none)" alone would conclude the system is unrepairable.
            spoken = self._run(root, "--list", JX_DEFAULT_ADMIN_NAMES="片区负责人")
            self.assertEqual(spoken.returncode, 0, spoken.stderr)
            self.assertIn("片区负责人", spoken.stdout)
            self.assertIn("兜底", spoken.stdout)


if __name__ == "__main__":
    unittest.main()
