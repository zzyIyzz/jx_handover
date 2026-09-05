"""Backup retention, in-process scheduler and account-login default tests.

Every test is self-contained: fake manifests live under temporary directories,
scheduler internals are mocked, and import-time configuration defaults are
probed in child interpreters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from app import config
from app.services import backup as backup_service


def _write_fake_backup(
    root: Path, backup_id: str, created_at: str, reason: str
) -> Path:
    bundle = root / f"jx-handover-backup-{backup_id}.zip"
    bundle.write_bytes(b"fake-zip")
    manifest = root / f"jx-handover-backup-{backup_id}.json"
    manifest.write_text(
        json.dumps(
            {
                "backup_format": 2,
                "backup_id": backup_id,
                "created_at": created_at,
                "reason": reason,
                "bundle_file": bundle.name,
                "bundle_size": bundle.stat().st_size,
                "bundle_sha256": "0" * 64,
                "nas_state": "pending",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bundle


class PruneRetentionTest(unittest.TestCase):
    def test_prune_keeps_newest_daily_and_manual_within_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            root = snapshot / "full_backups"
            root.mkdir(parents=True)
            daily = [
                _write_fake_backup(
                    root,
                    f"202608{day:02d}-000000-aaaaaaa{day}",
                    f"2026-08-{day:02d}T01:00:00+08:00",
                    "daily",
                )
                for day in range(1, 6)
            ]
            manual = [
                _write_fake_backup(
                    root,
                    f"202608{day:02d}-120000-bbbbbbb{day}",
                    f"2026-08-{day:02d}T12:00:00+08:00",
                    "manual",
                )
                for day in range(1, 4)
            ]
            with mock.patch.object(config, "SNAPSHOT_DIR", snapshot), \
                    mock.patch.object(config, "BACKUP_KEEP_DAILY", 2), \
                    mock.patch.object(config, "BACKUP_KEEP_MANUAL", 2):
                result = backup_service.prune_full_backups()

            # daily 保留 8/5、8/4，删 3 份；manual 保留 8/3、8/2，删 1 份。
            self.assertEqual(len(result["removed"]), 4)
            self.assertTrue(daily[4].exists())
            self.assertTrue(daily[3].exists())
            self.assertFalse(daily[0].exists())
            self.assertFalse(daily[1].exists())
            self.assertFalse(daily[2].exists())
            self.assertTrue(manual[2].exists())
            self.assertTrue(manual[1].exists())
            self.assertFalse(manual[0].exists())
            # 被清理备份的清单同时删除，不留孤儿 JSON。
            survivors = sorted(path.name for path in root.glob("*.json"))
            self.assertEqual(len(survivors), 4)

    def test_prune_skips_unreadable_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            root = snapshot / "full_backups"
            root.mkdir(parents=True)
            broken = root / "jx-handover-backup-20260801-000000-cccccccc.json"
            broken.write_text("{not-json", encoding="utf-8")
            with mock.patch.object(config, "SNAPSHOT_DIR", snapshot), \
                    mock.patch.object(config, "BACKUP_KEEP_DAILY", 1), \
                    mock.patch.object(config, "BACKUP_KEEP_MANUAL", 1):
                result = backup_service.prune_full_backups()
            self.assertTrue(broken.exists())
            self.assertIn("20260801-000000-cccccccc", result["skipped"])


class AutoBackupCycleTest(unittest.TestCase):
    def test_cycle_runs_daily_backup_then_prune(self):
        with mock.patch.object(
            backup_service, "maybe_daily_backup",
            return_value={"backup_id": "x"},
        ) as daily, mock.patch.object(
            backup_service, "prune_full_backups",
            return_value={"removed": ["old"]},
        ) as prune:
            state = backup_service.run_auto_backup_cycle()
        daily.assert_called_once_with()
        prune.assert_called_once_with()
        self.assertTrue(state["daily_created"])
        self.assertEqual(state["removed"], ["old"])
        self.assertEqual(state["last_error"], "")

    def test_cycle_records_error_and_reraises(self):
        with mock.patch.object(
            backup_service, "maybe_daily_backup",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaises(RuntimeError):
                backup_service.run_auto_backup_cycle()
        self.assertIn(
            "disk full", backup_service.auto_backup_status()["last_error"]
        )
        # 出错后恢复正常时错误信息被清空。
        with mock.patch.object(
            backup_service, "maybe_daily_backup", return_value=None
        ), mock.patch.object(
            backup_service, "prune_full_backups", return_value={"removed": []}
        ):
            backup_service.run_auto_backup_cycle()
        self.assertEqual(backup_service.auto_backup_status()["last_error"], "")

    def test_scheduler_starts_once(self):
        self.assertTrue(backup_service.start_auto_backup_scheduler())
        self.assertFalse(backup_service.start_auto_backup_scheduler())
        status = backup_service.auto_backup_status()
        self.assertTrue(status["scheduler_started"])
        self.assertEqual(status["keep_daily"], config.BACKUP_KEEP_DAILY)
        self.assertEqual(status["keep_manual"], config.BACKUP_KEEP_MANUAL)


class ServerAccountLoginDefaultTest(unittest.TestCase):
    """server 模式必须默认保留人员账号登录入口。"""

    def _flag(self, mode: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            for name in list(env):
                if name.startswith("JX_") or name in {
                    "WORD_TEMPLATE", "CLOUD_PUBLISH_DIR",
                }:
                    env.pop(name, None)
            env.update({
                "PYTHONPATH": str(BACKEND_ROOT),
                "JX_HANDOVER_MODE": mode,
                "JX_HANDOVER_DATA_DIR": tmp,
            })
            completed = subprocess.run(
                [
                    sys.executable, "-c",
                    "from app import config; print(config.ACCOUNT_LOGIN_ENABLED)",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        return completed.stdout.strip()

    def test_server_mode_keeps_personal_account_login(self):
        self.assertEqual(self._flag("server"), "True")

    def test_cloud_mode_keeps_personal_account_login(self):
        self.assertEqual(self._flag("cloud"), "True")

    def test_desktop_mode_stays_shared_login(self):
        self.assertEqual(self._flag("desktop"), "False")


if __name__ == "__main__":
    unittest.main()
