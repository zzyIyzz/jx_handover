"""Request-level audit middleware for the shared LAN service."""
from __future__ import annotations

import logging
import uuid

from fastapi import Request

from app.db import SessionLocal
from app.models import AuditEvent
from app.security import identity_from_request


logger = logging.getLogger(__name__)
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def audit_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    identity = identity_from_request(request)
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        if (
            request.method in MUTATING_METHODS
            and request.url.path.startswith("/api/")
            and not request.url.path.startswith("/api/session/")
        ):
            db = SessionLocal()
            try:
                db.add(AuditEvent(
                    actor_name=identity.name if identity else "",
                    actor_role=identity.role if identity else "anonymous",
                    method=request.method,
                    request_path=request.url.path[:500],
                    response_status=status_code,
                    client_ip=(request.client.host if request.client else "")[:100],
                    user_agent=request.headers.get("user-agent", "")[:500],
                    request_id=request_id,
                ))
                db.commit()
            except Exception:  # noqa: BLE001 - auditing must not break work
                db.rollback()
                logger.exception("Unable to persist audit event %s", request_id)
            finally:
                db.close()
