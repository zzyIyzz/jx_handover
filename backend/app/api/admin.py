"""Administrator diagnostics, verified backups and safe restore scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import AuditEvent, Staff
from app.security import (
    Identity,
    account_role,
    count_administrators,
    is_administrator,
    require_admin,
    reset_staff_password,
)
from app.services import data_root as data_root_service
from app.services.ai.adapter import ai_configuration_status, test_qwen_connection
from app.services.oss_status import oss_sync_status
from app.services.backup import (
    backup_status,
    cancel_scheduled_restore,
    create_full_backup,
    last_restore_result,
    list_full_backups,
    pending_restore_status,
    replicate_backup,
    replicate_pending_backups,
    schedule_restore,
    service_identity,
    test_nas_access,
    verify_full_backup,
)


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/ai")
def ai_status():
    return ai_configuration_status()


@router.post("/ai/test")
def ai_test():
    return test_qwen_connection()


@router.get("/audit")
def recent_audit(
    limit: int = 100,
    db: Session = Depends(get_db),
    _identity: Identity = Depends(require_admin),
):
    bounded = max(1, min(limit, 500))
    rows = (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .limit(bounded)
        .all()
    )
    return [{
        "id": row.id,
        "actor_name": row.actor_name,
        "actor_role": row.actor_role,
        "method": row.method,
        "request_path": row.request_path,
        "response_status": row.response_status,
        "client_ip": row.client_ip,
        "request_id": row.request_id,
        "created_at": row.created_at,
    } for row in rows]


def _account_row(staff: Staff) -> dict:
    return {
        "staff_id": staff.id,
        "name": staff.name,
        "station_code": staff.station_code,
        "staff_role": staff.role,
        "account_role": account_role(staff),
        "is_admin": bool(is_administrator(staff)),
        # A name pinned by the configured roster (JX_ADMIN_NAMES, or the
        # recovery roster when it is empty) is promoted on every start, so the
        # management page has to say why the toggle cannot simply be turned off.
        "admin_from_env": config.is_admin_name(staff.name),
        "is_active": bool(staff.is_active),
        "password_initialized": bool(staff.password_hash),
        "must_change_password": bool(staff.must_change_password),
        "password_updated_at": staff.password_updated_at,
        "last_login_at": staff.last_login_at,
    }


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
    return [
        _account_row(staff)
        for staff in db.query(Staff).order_by(Staff.name, Staff.id).all()
    ]


class StaffPatchReq(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


@router.patch("/accounts/{staff_id}")
def patch_account(
    staff_id: int,
    req: StaffPatchReq,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_admin),
):
    """Rename personnel, grant or revoke the administrator right, enable/disable them.

    Your own account and names pinned by JX_ADMIN_NAMES cannot be changed here,
    and the last remaining administrator can never be removed.  Without those
    guards one click could lock everybody out of the management page.
    """
    staff = db.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(404, "人员账号不存在。")
    if identity.staff_id == staff.id:
        raise HTTPException(409, "不能在人员管理中修改自己的账号。")
    if req.name is not None:
        clean_name = req.name.strip()
        if not clean_name:
            raise HTTPException(422, "人员姓名不能为空。")
        if clean_name != staff.name:
            if config.is_admin_name(staff.name):
                raise HTTPException(409, "该姓名在服务器配置的管理员名单（JX_ADMIN_NAMES，缺省 JX_DEFAULT_ADMIN_NAMES）中，不允许改名。")
            duplicate = (
                db.query(Staff)
                .filter(Staff.is_active == 1, Staff.name == clean_name)
                .first()
            )
            if duplicate is not None:
                raise HTTPException(409, "已有同名启用人员，请先调整姓名。")
            staff.name = clean_name
    if req.is_admin is not None and bool(req.is_admin) != bool(is_administrator(staff)):
        if req.is_admin:
            if not staff.is_active:
                raise HTTPException(409, "请先启用该账号，再授予管理员权限。")
            staff.is_admin = 1
        else:
            if config.is_admin_name(staff.name):
                raise HTTPException(
                    409,
                    "该姓名在服务器配置的管理员名单（JX_ADMIN_NAMES，缺省 JX_DEFAULT_ADMIN_NAMES）中，需先从环境变量中移除。",
                )
            if count_administrators(db) <= 1:
                raise HTTPException(409, "必须保留至少一个管理员，否则管理页与备份恢复将无法使用。")
            staff.is_admin = 0
            # Existing sessions keep the old admin claim until they are re-issued.
            staff.session_version = max(1, int(staff.session_version or 0)) + 1
    if req.is_active is not None and bool(req.is_active) != bool(staff.is_active):
        if req.is_active:
            duplicate = (
                db.query(Staff)
                .filter(Staff.is_active == 1, Staff.name == staff.name)
                .first()
            )
            if duplicate is not None:
                raise HTTPException(409, "已有同名启用人员，请先处理该账号后再启用。")
            staff.is_active = 1
            # Bump the version so sessions from before the disable never revive.
            staff.session_version = max(1, int(staff.session_version or 0)) + 1
        else:
            if config.is_admin_name(staff.name):
                raise HTTPException(409, "该姓名在服务器配置的管理员名单（JX_ADMIN_NAMES，缺省 JX_DEFAULT_ADMIN_NAMES）中，不允许停用。")
            if is_administrator(staff) and count_administrators(db) <= 1:
                raise HTTPException(409, "必须保留至少一个启用状态的管理员，不允许停用最后一个。")
            staff.is_active = 0
    db.commit()
    db.refresh(staff)
    return _account_row(staff)


@router.post("/accounts/{staff_id}/reset-password")
def reset_account_password(
    staff_id: int,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_admin),
):
    if not config.ACCOUNT_LOGIN_ENABLED:
        raise HTTPException(409, "当前运行模式未启用个人账号密码。")
    staff = db.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(404, "人员账号不存在。")
    if identity.staff_id == staff.id:
        raise HTTPException(422, "不能在管理页重置当前登录账号，请使用右上角“修改密码”。")
    reset_staff_password(db, staff)
    return _account_row(staff)


def _raise_backup_error(exc: Exception) -> None:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(422, str(exc)) from exc
    raise HTTPException(409, str(exc)) from exc


@router.post("/backup")
def backup_now():
    try:
        return create_full_backup(reason="manual")
    except Exception as exc:  # noqa: BLE001 - return an actionable UI message
        _raise_backup_error(exc)


@router.get("/backups")
def backups():
    return list_full_backups()


@router.post("/backups/sync-pending")
def sync_pending_backups():
    return replicate_pending_backups(limit=100)


@router.post("/backups/nas-test")
def nas_access_test():
    return test_nas_access()


@router.post("/backups/{backup_id}/verify")
def verify_backup(backup_id: str):
    try:
        return verify_full_backup(backup_id)
    except Exception as exc:  # noqa: BLE001 - translate service errors for UI
        _raise_backup_error(exc)


@router.post("/backups/{backup_id}/sync")
def sync_backup(backup_id: str):
    try:
        result = replicate_backup(backup_id)
    except Exception as exc:  # noqa: BLE001
        _raise_backup_error(exc)
    if result.get("nas_state") != "synced":
        raise HTTPException(409, result.get("nas_error") or "共享盘同步失败。")
    return result


@router.get("/restore")
def restore_state():
    return {
        "pending": pending_restore_status(),
        "last_result": last_restore_result(),
    }


@router.post("/backups/{backup_id}/restore/prepare")
def prepare_restore(
    backup_id: str,
    identity: Identity = Depends(require_admin),
):
    try:
        return schedule_restore(backup_id, requested_by=identity.name)
    except Exception as exc:  # noqa: BLE001
        _raise_backup_error(exc)


@router.delete("/restore/pending")
def cancel_restore():
    return cancel_scheduled_restore()


@router.get("/diagnostics")
def diagnostics(
    db: Session = Depends(get_db),
    _identity: Identity = Depends(require_admin),
):
    usage = shutil.disk_usage(config.USER_DATA_ROOT)
    database_check = "missing"
    database_size = 0
    if config.DATABASE_PATH.is_file():
        database_size = config.DATABASE_PATH.stat().st_size
        try:
            connection = sqlite3.connect(
                f"file:{config.DATABASE_PATH.as_posix()}?mode=ro", uri=True, timeout=10
            )
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
                database_check = str(row[0]) if row else "unknown"
            finally:
                connection.close()
        except sqlite3.Error as exc:
            database_check = f"error: {exc}"

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat(timespec="seconds")
    recent = (
        db.query(AuditEvent.client_ip, AuditEvent.actor_name)
        .filter(AuditEvent.created_at >= cutoff)
        .distinct()
        .all()
    )
    administrators = sorted(
        staff.name
        for staff in db.query(Staff).filter(Staff.is_active == 1).all()
        if is_administrator(staff)
    )
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": config.APP_VERSION,
        "account_login_enabled": config.ACCOUNT_LOGIN_ENABLED,
        "oss": oss_sync_status(),
        "mode": config.APP_MODE,
        "service_identity": service_identity(),
        "public_url": config.PUBLIC_URL,
        "data_root": str(config.USER_DATA_ROOT),
        "data_root_report": data_root_service.data_root_report(),
        "database_path": str(config.DATABASE_PATH),
        "database_size": database_size,
        "database_check": database_check,
        "disk_total": usage.total,
        "disk_used": usage.used,
        "disk_free": usage.free,
        "disk_free_percent": round(usage.free / usage.total * 100, 1) if usage.total else 0,
        "recent_users": len(recent),
        "administrators": administrators,
        "admin_configured": bool(administrators),
        "admin_env_names": sorted(config.ADMIN_NAMES),
        "ai": {
            "mode": config.AI_MODE,
            "mode_requested": config.AI_MODE_REQUESTED,
            "model": config.QWEN_MODEL,
            "base_url": config.QWEN_BASE_URL,
            "unavailable_reason": config.ai_unavailable_reason(),
        },
        "backup": backup_status(),
        "restore": {
            "pending": pending_restore_status(),
            "last_result": last_restore_result(),
        },
        "nas": {
            "configured": bool(config.NAS_BACKUP_DIR),
            "path": config.NAS_BACKUP_DIR,
        },
    }
