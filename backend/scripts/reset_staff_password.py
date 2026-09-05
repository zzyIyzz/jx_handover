"""Emergency local-console password reset for a single exact staff name."""
from __future__ import annotations

import argparse
import json
import uuid

from app import config
from app.db import SessionLocal
from app.models import AuditEvent, Staff
from app.security import reset_staff_password


def reset_by_exact_name(name: str) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("人员姓名不能为空。")
    if not config.ACCOUNT_LOGIN_ENABLED:
        raise RuntimeError("当前配置未启用个人账号模式。")
    db = SessionLocal()
    try:
        rows = (
            db.query(Staff)
            .filter(Staff.is_active == 1, Staff.name == clean_name)
            .order_by(Staff.id)
            .limit(2)
            .all()
        )
        if not rows:
            raise ValueError(f"没有找到启用人员：{clean_name}")
        if len(rows) != 1:
            raise ValueError(f"存在多个同名启用人员，拒绝重置：{clean_name}")
        staff = rows[0]
        reset_staff_password(db, staff)
        db.add(AuditEvent(
            actor_name="服务器管理员（本机命令行）",
            actor_role="admin",
            method="POST",
            request_path=f"/local-admin/accounts/{staff.id}/reset-password",
            response_status=200,
            client_ip="127.0.0.1",
            user_agent="reset_staff_password.py",
            request_id=uuid.uuid4().hex,
        ))
        db.commit()
        return {
            "status": "reset",
            "name": staff.name,
            "staff_id": staff.id,
            "must_change_password": True,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把一个人员账号重置为系统初始密码，并注销其全部旧会话。"
    )
    parser.add_argument("name", help="人员名单中的准确姓名")
    args = parser.parse_args()
    result = reset_by_exact_name(args.name)
    print(json.dumps(result, ensure_ascii=False))
    print("重置完成：下一次使用初始密码登录时，系统会强制设置新的个人密码。")


if __name__ == "__main__":
    main()
