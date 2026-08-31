"""Browser identity endpoints for the LAN edition."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Staff
from app.security import (
    COOKIE_NAME,
    authenticate_staff,
    identity_from_request,
    issue_session,
)


router = APIRouter(prefix="/api/session", tags=["session"])


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    access_code: str = Field(default="", max_length=256)


def _identity_payload(identity) -> dict:
    return {
        "name": identity.name,
        "role": identity.role,
        "staff_id": identity.staff_id,
    }


@router.get("/options")
def session_options(db: Session = Depends(get_db)):
    names = [
        row.name
        for row in db.query(Staff)
        .filter(Staff.is_active == 1)
        .order_by(Staff.name, Staff.id)
        .all()
    ]
    return {
        "auth_required": config.AUTH_REQUIRED,
        "access_code_required": bool(config.ACCESS_CODE),
        "mode": config.APP_MODE,
        "staff_names": list(dict.fromkeys(names)),
    }


@router.get("/me")
def current_session(request: Request):
    identity = identity_from_request(request)
    if identity is None and not config.AUTH_REQUIRED:
        return {"authenticated": True, "name": "本机用户", "role": "admin"}
    if identity is None:
        return {"authenticated": False}
    return {"authenticated": True, **_identity_payload(identity)}


@router.post("/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    identity = authenticate_staff(
        db, name=req.name, access_code=req.access_code
    )
    token = issue_session(identity)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    return {"authenticated": True, **_identity_payload(identity)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}
