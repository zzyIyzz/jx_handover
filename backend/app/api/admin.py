"""Administrator diagnostics. Secrets are never returned."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditEvent
from app.security import Identity, require_admin
from app.services.ai.adapter import ai_configuration_status, test_qwen_connection
from app.services.backup import create_database_backup


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


@router.post("/backup")
def backup_now():
    return create_database_backup(reason="manual")
