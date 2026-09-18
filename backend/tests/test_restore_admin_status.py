"""Recovery API describes the current host, including legacy pending markers."""
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import admin


def test_linux_pending_replaces_legacy_windows_instructions_without_mutation():
    marker = {"state": "pending_restart", "backup_id": "backup-test",
              "instruction": "请在服务器控制器中点击重启服务器"}
    with mock.patch.object(admin.sys, "platform", "linux"), \
         mock.patch.object(admin, "pending_restore_status", return_value=marker), \
         mock.patch.object(admin, "last_restore_result", return_value=None):
        result = admin.restore_state()
    assert "systemd" in result["pending"]["instruction"]
    assert "服务器控制器" not in result["pending"]["instruction"]
    assert result["pending"]["instruction"] == result["restart_instruction"]
    assert marker["instruction"] == "请在服务器控制器中点击重启服务器"


def test_windows_instructions_do_not_request_machine_reboot():
    with mock.patch.object(admin.sys, "platform", "win32"):
        instruction = admin.restore_restart_instruction()
    assert "服务端控制器" in instruction
    assert "无需重启整台服务器" in instruction


def test_invalid_pending_and_last_result_remain_visible():
    with mock.patch.object(admin, "pending_restore_status", return_value={"state": "invalid", "error": "bad marker"}), \
         mock.patch.object(admin, "last_restore_result", return_value={"state": "failed", "error": "bad archive"}):
        result = admin.restore_state()
    assert result["pending"]["state"] == "invalid"
    assert result["pending"]["error"] == "bad marker"
    assert result["last_result"]["error"] == "bad archive"
