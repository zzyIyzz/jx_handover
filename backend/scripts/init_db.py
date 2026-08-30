"""Initialize/migrate the local database (idempotent)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import initialize_application_data  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Staff, Station  # noqa: E402


def main() -> None:
    result = initialize_application_data()
    print("数据库初始化完成。")
    if result["migration"]["backup_path"]:
        print(f"迁移前备份：{result['migration']['backup_path']}")
    db = SessionLocal()
    try:
        for station in db.query(Station).all():
            print(f"  {station.code}  {station.name}")
        print(f"人员字典 {db.query(Staff).count()} 人")
    finally:
        db.close()


if __name__ == "__main__":
    main()

