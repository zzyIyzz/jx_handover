"""V0.5.x cloud security, container contract and cross-platform tests."""
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app import config
from app.security import LoginAttemptLimiter
from app.services.backup import _rewrite_snapshot_paths


class CloudProcessProbe(unittest.TestCase):
    def _environment(self, data_root: str) -> dict[str, str]:
        env = os.environ.copy()
        for name in list(env):
            if name.startswith(("JX_", "AI_", "QWEN_")):
                env.pop(name, None)
        env.update({
            "PYTHONPATH": str(BACKEND_ROOT),
            "JX_HANDOVER_MODE": "cloud",
            "JX_HANDOVER_DATA_DIR": data_root,
            "JX_PUBLIC_URL": "https://handover.example.test:1215",
            "JX_TRUSTED_HOSTS": "handover.example.test,127.0.0.1,localhost",
            "JX_CLOUD_ACCESS_SCOPE": "private",
            "JX_AUTH_REQUIRED": "1",
            "JX_COOKIE_SECURE": "1",
            "JX_ACCOUNT_LOGIN_ENABLED": "1",
            "JX_INITIAL_ACCOUNT_PASSWORD": "aaaa0000*",
            "JX_ACCESS_CODE": "",
            "JX_SESSION_SECRET": "s" * 48,
            "JX_ADMIN_NAMES": "测试管理员",
            "JX_SESSION_TTL_HOURS": "12",
            "AI_MODE": "mock",
        })
        return env

    def _run(self, script: str, env: dict[str, str]) -> dict:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_valid_cloud_configuration_and_minimal_health_contract(self):
        script = r'''
import json
from fastapi.testclient import TestClient
from app import config
config.validate_runtime_configuration()
from app.main import app
from app.db import SessionLocal
from app.migrations import initialize_database
from app.models import Staff
from app.security import hash_password
initialize_database()
db = SessionLocal()
db.add(Staff(
    station_code="TEST",
    name="测试管理员",
    role="测试角色",
    note="",
    password_hash=hash_password("Cloud-test-password-2026!"),
    must_change_password=0,
))
db.commit()
db.close()
with TestClient(app, base_url="https://handover.example.test:1215") as client:
    health = client.get("/api/health")
    options = client.get("/api/session/options")
    foreign = client.post(
        "/api/session/login",
        headers={"Origin": "https://lookalike.example.test:1215"},
        json={"name": "测试管理员", "password": "Cloud-test-password-2026!"},
    )
    missing_public_port = client.post(
        "/api/session/login",
        headers={"Origin": "https://handover.example.test"},
        json={"name": "测试管理员", "password": "Cloud-test-password-2026!"},
    )
    login = client.post(
        "/api/session/login",
        headers={"Origin": "https://handover.example.test:1215"},
        json={"name": "测试管理员", "password": "Cloud-test-password-2026!"},
    )
    protected = client.get("/api/handovers")
    bad_host = client.get("/api/health", headers={"Host": "evil.example.test"})
    docs = client.get("/docs")
    print(json.dumps({
        "mode": config.APP_MODE,
        "host": config.APP_HOST,
        "public_url": config.PUBLIC_URL,
        "cookie_secure": config.COOKIE_SECURE,
        "health_status": health.status_code,
        "health": health.json(),
        "options": options.json(),
        "hsts": health.headers.get("strict-transport-security", ""),
        "csp": health.headers.get("content-security-policy", ""),
        "foreign_status": foreign.status_code,
        "missing_public_port_status": missing_public_port.status_code,
        "login_status": login.status_code,
        "set_cookie": login.headers.get("set-cookie", ""),
        "protected_status": protected.status_code,
        "bad_host_status": bad_host.status_code,
        "docs_status": docs.status_code,
    }, ensure_ascii=False))
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(script, self._environment(tmp))

        self.assertEqual(result["mode"], "cloud")
        self.assertEqual(result["host"], "0.0.0.0")
        self.assertEqual(result["public_url"], "https://handover.example.test:1215")
        self.assertTrue(result["cookie_secure"])
        self.assertEqual(result["health_status"], 200)
        self.assertEqual(result["health"]["status"], "ok")
        self.assertEqual(result["health"]["version"], "0.5.1")
        self.assertEqual(result["health"]["mode"], "cloud")
        self.assertEqual(result["health"]["port"], 8765)
        self.assertEqual(result["health"]["public_port"], 1215)
        self.assertEqual(result["health"]["login_mode"], "account")
        self.assertNotIn("data_root", result["health"])
        self.assertNotIn("public_url", result["health"])
        self.assertEqual(result["options"]["login_mode"], "account")
        self.assertEqual(result["options"]["staff_names"], [])
        self.assertIn("max-age=31536000", result["hsts"])
        self.assertIn("frame-ancestors 'none'", result["csp"])
        self.assertEqual(result["foreign_status"], 403)
        self.assertEqual(result["missing_public_port_status"], 403)
        self.assertEqual(result["login_status"], 200)
        self.assertIn("secure", result["set_cookie"].lower())
        self.assertIn("httponly", result["set_cookie"].lower())
        self.assertIn("samesite=strict", result["set_cookie"].lower())
        self.assertEqual(result["protected_status"], 200)
        self.assertEqual(result["bad_host_status"], 400)
        self.assertEqual(result["docs_status"], 404)

    def test_cloud_configuration_fails_closed_with_actionable_errors(self):
        script = r'''
import json
from app import config
try:
    config.validate_runtime_configuration()
except RuntimeError as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
else:
    print(json.dumps({"error": ""}, ensure_ascii=False))
'''
        with tempfile.TemporaryDirectory() as tmp:
            env = self._environment(tmp)
            env.update({
                "JX_PUBLIC_URL": "http://handover.example.test:1215/path",
                "JX_TRUSTED_HOSTS": "*",
                "JX_CLOUD_ACCESS_SCOPE": "public",
                "JX_AUTH_REQUIRED": "0",
                "JX_COOKIE_SECURE": "0",
                "JX_INITIAL_ACCOUNT_PASSWORD": "short",
                "JX_SESSION_SECRET": "short",
                "JX_ADMIN_NAMES": "",
                "JX_SESSION_TTL_HOURS": "168",
            })
            error = self._run(script, env)["error"]

        self.assertIn("HTTPS 地址", error)
        self.assertIn("JX_AUTH_REQUIRED", error)
        self.assertIn("JX_INITIAL_ACCOUNT_PASSWORD 至少需要 8", error)
        self.assertIn("至少需要 32", error)
        self.assertIn("JX_COOKIE_SECURE", error)
        self.assertIn("不能超过 24", error)
        self.assertIn("至少需要配置一名", error)
        self.assertIn("必须为 private", error)
        self.assertIn("不允许使用通配符", error)

    def test_example_placeholder_values_cannot_start_cloud_service(self):
        script = r'''
import json
from app import config
try:
    config.validate_runtime_configuration()
except RuntimeError as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
else:
    print(json.dumps({"error": ""}, ensure_ascii=False))
'''
        with tempfile.TemporaryDirectory() as tmp:
            env = self._environment(tmp)
            env.update({
                "JX_PUBLIC_URL": "https://handover.example.com:1215",
                "JX_TRUSTED_HOSTS": "handover.example.com,127.0.0.1,localhost",
                "JX_ACCOUNT_LOGIN_ENABLED": "0",
                "JX_ACCESS_CODE": "请替换为至少12位的随机访问口令",
                "JX_SESSION_SECRET": "请替换" * 16,
                "JX_ADMIN_NAMES": "请填写实际管理员姓名",
            })
            error = self._run(script, env)["error"]
        self.assertIn("示例域名", error)
        self.assertIn("JX_ACCOUNT_LOGIN_ENABLED 必须为 1", error)
        self.assertIn("示例占位文字，请生成真实随机口令", error)
        self.assertIn("示例占位文字，请生成真实随机密钥", error)
        self.assertIn("示例占位文字，请填写实际管理员", error)

    def test_example_or_private_ip_cannot_start_cloud_service(self):
        script = r'''
import json
from app import config
try:
    config.validate_runtime_configuration()
except RuntimeError as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
else:
    print(json.dumps({"error": ""}, ensure_ascii=False))
'''
        with tempfile.TemporaryDirectory() as tmp:
            for address in ("203.0.113.20", "192.168.14.52"):
                env = self._environment(tmp)
                env.update({
                    "JX_PUBLIC_URL": f"https://{address}:1215",
                    "JX_TRUSTED_HOSTS": f"{address},127.0.0.1,localhost",
                })
                error = self._run(script, env)["error"]
                self.assertIn("真实公网 IPv4", error)
            env = self._environment(tmp)
            env["JX_PUBLIC_URL"] = "https://handover.example.test"
            error = self._run(script, env)["error"]
            self.assertIn("显式包含公网 HTTPS 端口 :1215", error)


class CloudSecurityUnitTest(unittest.TestCase):
    def test_login_limiter_blocks_then_expires_and_success_resets(self):
        limiter = LoginAttemptLimiter(
            max_failures=3,
            window_seconds=10,
            block_seconds=20,
        )
        self.assertEqual(limiter.record_failure("198.51.100.8", now=100), 0)
        self.assertEqual(limiter.record_failure("198.51.100.8", now=101), 0)
        self.assertEqual(limiter.record_failure("198.51.100.8", now=102), 20)
        self.assertEqual(limiter.retry_after("198.51.100.8", now=103), 19)
        self.assertEqual(limiter.retry_after("198.51.100.8", now=123), 0)

        limiter.record_failure("198.51.100.9", now=200)
        limiter.record_success("198.51.100.9")
        self.assertEqual(limiter.retry_after("198.51.100.9", now=201), 0)
        self.assertEqual(limiter.record_failure("198.51.100.9", now=202), 0)

    def test_windows_snapshot_path_is_rewritten_after_linux_style_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated"
            document = generated / "TEST_STATION" / "202608" / "历史版本.docx"
            document.parent.mkdir(parents=True)
            document.write_bytes(b"word")
            database = root / "handover.db"
            connection = sqlite3.connect(str(database))
            connection.execute(
                "CREATE TABLE document_snapshots (id TEXT PRIMARY KEY, docx_path TEXT)"
            )
            connection.execute(
                "INSERT INTO document_snapshots VALUES (?, ?)",
                (
                    "snap-1",
                    r"D:\旧服务器\JXHandover\generated\TEST_STATION\202608\历史版本.docx",
                ),
            )
            connection.commit()
            connection.close()

            with mock.patch.object(config, "GENERATED_DIR", generated):
                updated = _rewrite_snapshot_paths(database)

            connection = sqlite3.connect(str(database))
            try:
                stored = connection.execute(
                    "SELECT docx_path FROM document_snapshots WHERE id='snap-1'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(updated, 1)
            self.assertEqual(stored, str(document))

    def test_compose_never_publishes_application_port_to_all_interfaces(self):
        compose = (PROJECT_ROOT / "deploy" / "cloud" / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        dockerfile = (PROJECT_ROOT / "deploy" / "cloud" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        entrypoint = (
            PROJECT_ROOT / "deploy" / "cloud" / "docker-entrypoint.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8765:8765"', compose)
        self.assertNotIn('"8765:8765"', compose)
        self.assertNotIn("127.0.0.1:1215", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("--workers 1", entrypoint)
        self.assertIn("stat -f -c %T", entrypoint)
        self.assertIn("nfs|nfs4|cifs|smb*|fuse*|9p", entrypoint)
        self.assertIn("os.environ['JX_PUBLIC_URL']", compose)

        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("deploy/cloud/.env", dockerignore)
        self.assertIn("deploy/cloud/oss-backup.env", dockerignore)
        self.assertIn("deploy/cloud/ossutilconfig", dockerignore)

        deploy_script = (
            PROJECT_ROOT / "deploy" / "cloud" / "scripts" / "deploy.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("正式数据目录不存在", deploy_script)
        self.assertIn("prepare-host.sh", deploy_script)
        self.assertIn("内部端口 8765 已被其他程序占用", deploy_script)
        self.assertIn("公网端口 1215 已被占用", deploy_script)
        self.assertIn("http://127.0.0.1:8765/api/health", deploy_script)

        prepare_script = (
            PROJECT_ROOT / "deploy" / "cloud" / "scripts" / "prepare-host.sh"
        ).read_text(encoding="utf-8")
        certificate_script = (
            PROJECT_ROOT
            / "deploy"
            / "cloud"
            / "scripts"
            / "install-ip-certificate.sh"
        ).read_text(encoding="utf-8")
        nginx_configure_script = (
            PROJECT_ROOT
            / "deploy"
            / "cloud"
            / "scripts"
            / "configure-ip-nginx.sh"
        ).read_text(encoding="utf-8")
        ip_nginx = (
            PROJECT_ROOT
            / "deploy"
            / "cloud"
            / "nginx"
            / "jx-handover-ip-server.conf.example"
        ).read_text(encoding="utf-8")
        self.assertIn("--ip", prepare_script)
        self.assertIn(".env.ip.example", prepare_script)
        self.assertIn("--cert-profile shortlived", certificate_script)
        self.assertIn("--install-cronjob", certificate_script)
        self.assertIn("acme.sh.*--cron", certificate_script)
        self.assertIn("before-jx-", nginx_configure_script)
        self.assertIn("已恢复原宝塔配置", nginx_configure_script)
        self.assertIn("公网端口 1215 已被其他程序占用", nginx_configure_script)
        self.assertIn("listener_has_non_nginx_process", nginx_configure_script)
        self.assertIn("listener_is_public_nginx_only", nginx_configure_script)
        self.assertIn("Nginx 重载失败", nginx_configure_script)
        self.assertIn("原文件已恢复，但 Nginx 恢复重载失败", nginx_configure_script)
        self.assertIn("没有成功监听公网端口 1215", nginx_configure_script)
        self.assertIn("公网 HTTPS 1215", nginx_configure_script)
        self.assertIn("内部 127.0.0.1:8765", nginx_configure_script)
        self.assertIn("listen 1215 ssl", ip_nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8765", ip_nginx)
        domain_nginx = (
            PROJECT_ROOT
            / "deploy"
            / "cloud"
            / "nginx"
            / "jx-handover-server.conf.example"
        ).read_text(encoding="utf-8")
        location_nginx = (
            PROJECT_ROOT
            / "deploy"
            / "cloud"
            / "nginx"
            / "jx-handover-location.conf.example"
        ).read_text(encoding="utf-8")
        for nginx_text in (ip_nginx, domain_nginx, location_nginx):
            # 非标准 HTTPS 端口必须把 Host（含端口）与转发端口传给后端。
            self.assertIn("proxy_set_header Host $host:1215;", nginx_text)
            self.assertIn("proxy_set_header X-Forwarded-Host $host:1215;", nginx_text)
            self.assertIn("proxy_set_header X-Forwarded-Port 1215;", nginx_text)
            self.assertNotIn("$http_host", nginx_text)


if __name__ == "__main__":
    unittest.main()
