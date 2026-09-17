"""Read host-generated OSS receipts; never infer OSS success from NAS state."""
from datetime import datetime, timezone
import json

from app import config


def oss_sync_status() -> dict:
    path = config.SNAPSHOT_DIR / "oss-sync-status.json"
    empty = {"state": "unknown", "message": "尚未收到 OSS 同步记录，请配置新版同步脚本。",
             "updated_at": None, "last_success_at": None, "synced_count": 0,
             "latest_backup_at": None, "target": "", "stale": False}
    if not path.exists():
        return empty
    try:
        if path.stat().st_size > 65536:
            raise ValueError("oversized")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("state") not in {"running", "success", "failed"}:
            raise ValueError("invalid state")
        updated = datetime.fromisoformat(data["updated_at"])
        if updated.tzinfo is None:
            raise ValueError("missing timezone")
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if age < -300:
            raise ValueError("future receipt")
        result = {key: data.get(key, value) for key, value in empty.items()}
        result["synced_count"] = max(0, int(result["synced_count"]))
        # No arbitrary log/error contents or credentials are returned to UI.
        result["message"] = {
            "running": "正在上传备份。", "success": "备份包与清单已由 OSS 工具上传完成。",
            "failed": "同步失败，请检查宝塔任务日志、OSS 权限及本地备份。",
        }[result["state"]]
        result["stale"] = age > (2 * 3600 if result["state"] == "running" else 26 * 3600)
        if result["stale"]:
            result["message"] = "同步记录已过期，请检查定时任务是否持续运行。"
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**empty, "state": "invalid", "message": "OSS 同步记录无法读取，请检查文件权限或重新执行同步任务。"}
