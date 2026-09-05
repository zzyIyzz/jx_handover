"""Validate cloud settings and apply a scheduled restore before Uvicorn."""
from __future__ import annotations

import json

from app import config
from app.services.backup import apply_pending_restore


def main() -> None:
    config.validate_runtime_configuration()
    restored = apply_pending_restore()
    print(json.dumps({
        "status": "ready",
        "version": config.APP_VERSION,
        "mode": config.APP_MODE,
        "restore": (
            {
                "state": restored.get("state"),
                "backup_id": restored.get("backup_id"),
                "pre_restore_backup_id": restored.get("pre_restore_backup_id"),
            }
            if restored
            else None
        ),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
