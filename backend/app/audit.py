"""Request-level audit middleware tied to the authenticated staff account."""
from __future__ import annotations

import logging
import uuid

from fastapi import Request

from app.db import SessionLocal
from app.models import AuditEvent
from app.security import identity_from_request, validated_identity_from_request


logger = logging.getLogger(__name__)
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def audit_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    identity = identity_from_request(request)
    should_audit = (
        request.method in MUTATING_METHODS
        and request.url.path.startswith("/api/")
        and request.url.path not in {
            "/api/session/login",
            "/api/session/logout",
        }
    )
    # A signed but invalidated cookie must not be reported as an authenticated
    # operator.  Validation happens before the request so password changes can
    # still be attributed after they deliberately invalidate the old token.
    if should_audit and identity is not None:
        validation_db = SessionLocal()
        try:
            identity = validated_identity_from_request(request, validation_db)
        except Exception:  # noqa: BLE001 - auditing must never block work
            logger.exception("Unable to validate audit identity %s", request_id)
            identity = None
        finally:
            validation_db.close()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        if should_audit:
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
