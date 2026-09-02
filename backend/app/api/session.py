"""Browser identity endpoints for personal accounts and legacy LAN access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Staff
from app.security import (
    COOKIE_NAME,
    assert_login_allowed,
    authenticate_staff,
    change_staff_password,
    issue_session,
    record_login_failure,
    record_login_success,
    require_session_identity,
    validated_identity_from_request,
    Identity,
)


router = APIRouter(prefix="/api/session", tags=["session"])


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(default="", max_length=128)
    access_code: str = Field(default="", max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


def _identity_payload(identity: Identity) -> dict:
    return {
        "name": identity.name,
        "role": identity.role,
        "staff_id": identity.staff_id,
        "password_change_required": identity.password_change_required,
    }


def _set_session_cookie(response: Response, identity: Identity) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_session(identity),
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=config.COOKIE_SECURE,
        path="/",
    )


@router.get("/options")
def session_options(db: Session = Depends(get_db)):
    names = []
    if not config.ACCOUNT_LOGIN_ENABLED:
        names = [
            row.name
            for row in db.query(Staff)
            .filter(Staff.is_active == 1)
            .order_by(Staff.name, Staff.id)
            .all()
        ]
    return {
        "auth_required": config.AUTH_REQUIRED,
        "login_mode": "account" if config.ACCOUNT_LOGIN_ENABLED else "shared",
        "access_code_required": bool(
            config.ACCESS_CODE and not config.ACCOUNT_LOGIN_ENABLED
        ),
        "mode": config.APP_MODE,
        "staff_names": list(dict.fromkeys(names)),
    }


@router.get("/me")
def current_session(request: Request, db: Session = Depends(get_db)):
    identity = validated_identity_from_request(request, db)
    if identity is None and not config.AUTH_REQUIRED:
        return {"authenticated": True, "name": "本机用户", "role": "admin"}
    if identity is None:
        return {"authenticated": False}
    return {"authenticated": True, **_identity_payload(identity)}


@router.post("/login")
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    login_keys = assert_login_allowed(request, req.name)
    try:
        identity = authenticate_staff(
            db,
            name=req.name,
            password=req.password,
            access_code=req.access_code,
        )
    except HTTPException:
        record_login_failure(login_keys)
        raise
    record_login_success(login_keys)
    _set_session_cookie(response, identity)
    return {"authenticated": True, **_identity_payload(identity)}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_session_identity),
):
    updated = change_staff_password(
        db,
        identity=identity,
        current_password=req.current_password,
        new_password=req.new_password,
    )
    _set_session_cookie(response, updated)
    return {"authenticated": True, **_identity_payload(updated)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=config.COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )
    return {"authenticated": False}
