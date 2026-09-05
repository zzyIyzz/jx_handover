"""Personal-account authentication, password lifecycle and audit tests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
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
from app.api import admin, handovers, session as session_api
from app.audit import audit_requests
from app.db import Base, get_db
from app.models import AuditEvent, Staff
from app.security import (
    COOKIE_NAME,
    hash_password,
    initialize_missing_staff_passwords,
    validate_account_directory,
    verify_password,
)
from scripts.reset_staff_password import reset_by_exact_name


INITIAL_PASSWORD = "aaaa0000*"
ADMIN_PASSWORD = "Admin-personal-2026!"
OPERATOR_PASSWORD = "Operator-personal-2026!"


class AccountAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        administrator = Staff(
            station_code="REGION",
            name="测试管理员",
            role="带班负责人",
            note="",
            password_hash=hash_password(INITIAL_PASSWORD),
            must_change_password=1,
            session_version=1,
        )
        operator = Staff(
            station_code="REGION",
            name="测试值班员",
            role="现场值守",
            note="",
            password_hash=hash_password(INITIAL_PASSWORD),
            must_change_password=1,
            session_version=1,
        )
        db.add_all([administrator, operator])
        db.commit()
        self.admin_id = administrator.id
        self.operator_id = operator.id
        self.admin_initial_hash = administrator.password_hash
        self.operator_initial_hash = operator.password_hash
        db.close()

        self.app = FastAPI()
        self.app.middleware("http")(audit_requests)
        self.app.include_router(session_api.router)
        self.app.include_router(admin.router)
        self.app.include_router(handovers.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.app.dependency_overrides[get_db] = override_db
        self.settings = {
            "APP_MODE": "cloud",
            "AUTH_REQUIRED": True,
            "ACCOUNT_LOGIN_ENABLED": True,
            "INITIAL_ACCOUNT_PASSWORD": INITIAL_PASSWORD,
            "ACCESS_CODE": "",
            "COOKIE_SECURE": True,
            "SESSION_SECRET": "account-test-session-secret-2026-very-long",
            "SESSION_TTL_HOURS": 12,
            "ADMIN_NAMES": {"测试管理员"},
        }

    def tearDown(self) -> None:
        self.engine.dispose()

    def _login(self, client: TestClient, name: str, password: str):
        return client.post(
            "/api/session/login",
            json={"name": name, "password": password},
        )

    def _change(self, client: TestClient, current: str, new: str):
        return client.post(
            "/api/session/change-password",
            json={"current_password": current, "new_password": new},
        )

    def test_first_login_forces_change_and_invalidates_old_session(self):
        self.assertNotEqual(self.admin_initial_hash, self.operator_initial_hash)
        self.assertTrue(self.admin_initial_hash.startswith("$argon2id$"))
        self.assertNotIn(INITIAL_PASSWORD, self.admin_initial_hash)

        with mock.patch.multiple(config, **self.settings), mock.patch(
            "app.audit.SessionLocal", self.Session
        ):
            with TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as client:
                options = client.get("/api/session/options")
                self.assertEqual(options.status_code, 200)
                self.assertEqual(options.json()["login_mode"], "account")
                self.assertEqual(options.json()["staff_names"], [])
                self.assertNotIn("带班负责人", json.dumps(options.json(), ensure_ascii=False))

                wrong = self._login(client, "不存在的人", "wrong-password")
                self.assertEqual(wrong.status_code, 401)
                self.assertEqual(wrong.json()["detail"], "账号或密码不正确。")

                login = self._login(client, "测试管理员", INITIAL_PASSWORD)
                self.assertEqual(login.status_code, 200)
                self.assertTrue(login.json()["password_change_required"])
                old_cookie = client.cookies.get(COOKIE_NAME)
                self.assertTrue(old_cookie)

                blocked = client.get("/api/handovers")
                self.assertEqual(blocked.status_code, 403)
                self.assertEqual(
                    blocked.json()["detail"]["code"],
                    "PASSWORD_CHANGE_REQUIRED",
                )

                wrong_current = self._change(client, "not-current", ADMIN_PASSWORD)
                self.assertEqual(wrong_current.status_code, 401)
                same_initial = self._change(client, INITIAL_PASSWORD, INITIAL_PASSWORD)
                self.assertEqual(same_initial.status_code, 422)
                too_short = self._change(client, INITIAL_PASSWORD, "too-short")
                self.assertEqual(too_short.status_code, 422)

                changed = self._change(client, INITIAL_PASSWORD, ADMIN_PASSWORD)
                self.assertEqual(changed.status_code, 200)
                self.assertFalse(changed.json()["password_change_required"])
                self.assertEqual(client.get("/api/handovers").status_code, 200)

                current_cookie = client.cookies.get(COOKIE_NAME)
                self.assertNotEqual(old_cookie, current_cookie)

            with TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as stale_client:
                stale_client.cookies.set(COOKIE_NAME, old_cookie)
                stale = stale_client.get("/api/handovers")
                self.assertEqual(stale.status_code, 401)
                self.assertEqual(stale.json()["detail"]["code"], "LOGIN_REQUIRED")

        db = self.Session()
        try:
            administrator = db.get(Staff, self.admin_id)
            self.assertIsNotNone(administrator.last_login_at)
            self.assertIsNotNone(administrator.password_updated_at)
            self.assertEqual(administrator.must_change_password, 0)
            self.assertEqual(administrator.session_version, 2)
            self.assertTrue(verify_password(ADMIN_PASSWORD, administrator.password_hash))
            self.assertFalse(verify_password(INITIAL_PASSWORD, administrator.password_hash))
            successful_changes = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.actor_name == "测试管理员",
                    AuditEvent.request_path == "/api/session/change-password",
                    AuditEvent.response_status == 200,
                )
                .count()
            )
            self.assertEqual(successful_changes, 1)
        finally:
            db.close()

    def test_admin_can_reset_another_account_and_new_staff_gets_safe_default(self):
        with mock.patch.multiple(config, **self.settings), mock.patch(
            "app.audit.SessionLocal", self.Session
        ):
            with TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as admin_client, TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as operator_client:
                self.assertEqual(
                    self._login(admin_client, "测试管理员", INITIAL_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._change(admin_client, INITIAL_PASSWORD, ADMIN_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._login(operator_client, "测试值班员", INITIAL_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._change(
                        operator_client,
                        INITIAL_PASSWORD,
                        OPERATOR_PASSWORD,
                    ).status_code,
                    200,
                )

                accounts = admin_client.get("/api/admin/accounts")
                self.assertEqual(accounts.status_code, 200)
                serialized = json.dumps(accounts.json(), ensure_ascii=False)
                self.assertNotIn("password_hash", serialized)
                self.assertNotIn(INITIAL_PASSWORD, serialized)
                self.assertNotIn(ADMIN_PASSWORD, serialized)

                created = admin_client.post("/api/staff", json={
                    "station_code": "REGION",
                    "name": "新增值班员",
                    "role": "现场值守",
                    "note": "",
                })
                self.assertEqual(created.status_code, 200)
                duplicate = admin_client.post("/api/staff", json={
                    "station_code": "REGION",
                    "name": "新增值班员",
                    "role": "现场值守",
                    "note": "",
                })
                self.assertEqual(duplicate.status_code, 409)
                blank = admin_client.post("/api/staff", json={
                    "station_code": "REGION",
                    "name": "   ",
                    "role": "现场值守",
                    "note": "",
                })
                self.assertEqual(blank.status_code, 422)

                self_reset = admin_client.post(
                    f"/api/admin/accounts/{self.admin_id}/reset-password"
                )
                self.assertEqual(self_reset.status_code, 422)

                reset = admin_client.post(
                    f"/api/admin/accounts/{self.operator_id}/reset-password"
                )
                self.assertEqual(reset.status_code, 200)
                self.assertTrue(reset.json()["must_change_password"])

                invalidated = operator_client.get("/api/handovers")
                self.assertEqual(invalidated.status_code, 401)
                self.assertEqual(
                    self._login(
                        operator_client,
                        "测试值班员",
                        OPERATOR_PASSWORD,
                    ).status_code,
                    401,
                )
                default_login = self._login(
                    operator_client,
                    "测试值班员",
                    INITIAL_PASSWORD,
                )
                self.assertEqual(default_login.status_code, 200)
                self.assertTrue(default_login.json()["password_change_required"])

        db = self.Session()
        try:
            added = db.query(Staff).filter(Staff.name == "新增值班员").one()
            self.assertTrue(added.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(INITIAL_PASSWORD, added.password_hash))
            self.assertEqual(added.must_change_password, 1)
            reset_events = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.actor_name == "测试管理员",
                    AuditEvent.request_path
                    == f"/api/admin/accounts/{self.operator_id}/reset-password",
                    AuditEvent.response_status == 200,
                )
                .count()
            )
            self.assertEqual(reset_events, 1)
        finally:
            db.close()

    def test_admin_can_rename_and_disable_staff_while_guards_hold(self):
        with mock.patch.multiple(config, **self.settings), mock.patch(
            "app.audit.SessionLocal", self.Session
        ):
            with TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as admin_client, TestClient(
                self.app,
                base_url="https://handover.example.test:1215",
            ) as operator_client:
                self.assertEqual(
                    self._login(admin_client, "测试管理员", INITIAL_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._change(admin_client, INITIAL_PASSWORD, ADMIN_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._login(operator_client, "测试值班员", INITIAL_PASSWORD).status_code,
                    200,
                )
                self.assertEqual(
                    self._change(
                        operator_client, INITIAL_PASSWORD, OPERATOR_PASSWORD
                    ).status_code,
                    200,
                )

                forbidden_add = operator_client.post("/api/staff", json={
                    "station_code": "REGION",
                    "name": "越权人员",
                })
                self.assertEqual(forbidden_add.status_code, 403)
                forbidden_patch = operator_client.patch(
                    f"/api/admin/accounts/{self.operator_id}",
                    json={"name": "越权改名"},
                )
                self.assertEqual(forbidden_patch.status_code, 403)

                renamed = admin_client.patch(
                    f"/api/admin/accounts/{self.operator_id}",
                    json={"name": "测试值班员二"},
                )
                self.assertEqual(renamed.status_code, 200)
                self.assertEqual(renamed.json()["name"], "测试值班员二")
                self.assertEqual(operator_client.get("/api/handovers").status_code, 401)
                self.assertEqual(
                    self._login(
                        operator_client, "测试值班员", OPERATOR_PASSWORD
                    ).status_code,
                    401,
                )
                self.assertEqual(
                    self._login(
                        operator_client, "测试值班员二", OPERATOR_PASSWORD
                    ).status_code,
                    200,
                )

                duplicate = admin_client.patch(
                    f"/api/admin/accounts/{self.operator_id}",
                    json={"name": "测试管理员"},
                )
                self.assertEqual(duplicate.status_code, 409)
                blank = admin_client.patch(
                    f"/api/admin/accounts/{self.operator_id}", json={"name": "  "}
                )
                self.assertEqual(blank.status_code, 422)

                admin_rename = admin_client.patch(
                    f"/api/admin/accounts/{self.admin_id}", json={"name": "新管理员"}
                )
                self.assertEqual(admin_rename.status_code, 409)
                admin_disable = admin_client.patch(
                    f"/api/admin/accounts/{self.admin_id}", json={"is_active": False}
                )
                self.assertEqual(admin_disable.status_code, 409)

                disabled = admin_client.patch(
                    f"/api/admin/accounts/{self.operator_id}",
                    json={"is_active": False},
                )
                self.assertEqual(disabled.status_code, 200)
                self.assertFalse(disabled.json()["is_active"])
                self.assertEqual(operator_client.get("/api/handovers").status_code, 401)
                self.assertEqual(
                    self._login(
                        operator_client, "测试值班员二", OPERATOR_PASSWORD
                    ).status_code,
                    401,
                )
                enabled = admin_client.patch(
                    f"/api/admin/accounts/{self.operator_id}", json={"is_active": True}
                )
                self.assertEqual(enabled.status_code, 200)
                self.assertTrue(enabled.json()["is_active"])
                self.assertEqual(operator_client.get("/api/handovers").status_code, 401)
                self.assertEqual(
                    self._login(
                        operator_client, "测试值班员二", OPERATOR_PASSWORD
                    ).status_code,
                    200,
                )

    def test_account_directory_rejects_duplicate_names_and_missing_admin(self):
        db = self.Session()
        try:
            duplicate = Staff(
                station_code="OTHER",
                name="测试值班员",
                role="另一个岗位",
                note="",
            )
            uninitialized = Staff(
                station_code="REGION",
                name="待初始化账号",
                role="现场值守",
                note="",
            )
            db.add_all([duplicate, uninitialized])
            db.commit()
            with mock.patch.multiple(config, **self.settings):
                with self.assertRaisesRegex(RuntimeError, "同名启用人员"):
                    validate_account_directory(db)
                duplicate.is_active = 0
                db.commit()
                initialized = initialize_missing_staff_passwords(db)
                self.assertGreaterEqual(initialized, 1)
                self.assertTrue(
                    verify_password(INITIAL_PASSWORD, uninitialized.password_hash)
                )
                with mock.patch.object(config, "ADMIN_NAMES", {"不存在管理员"}):
                    with self.assertRaisesRegex(RuntimeError, "不在启用人员名单"):
                        validate_account_directory(db)
        finally:
            db.close()

    def test_local_console_can_recover_the_only_admin_account(self):
        with mock.patch.multiple(config, **self.settings), mock.patch(
            "scripts.reset_staff_password.SessionLocal", self.Session
        ):
            result = reset_by_exact_name("  测试管理员  ")
            self.assertEqual(result["status"], "reset")
            self.assertEqual(result["name"], "测试管理员")
            with self.assertRaisesRegex(ValueError, "没有找到启用人员"):
                reset_by_exact_name("不存在人员")

        db = self.Session()
        try:
            administrator = db.get(Staff, self.admin_id)
            self.assertEqual(administrator.must_change_password, 1)
            self.assertEqual(administrator.session_version, 2)
            self.assertTrue(
                verify_password(INITIAL_PASSWORD, administrator.password_hash)
            )
            event = (
                db.query(AuditEvent)
                .filter(AuditEvent.actor_name == "服务器管理员（本机命令行）")
                .one()
            )
            self.assertEqual(event.response_status, 200)
            self.assertIn("/local-admin/accounts/", event.request_path)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
