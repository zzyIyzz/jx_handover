"""Small, dependency-free LAN session tokens.

This is intentionally an identity and audit layer for a trusted internal
network, not an Internet-facing identity provider.  A shared access code may
be configured on the server.  The Qwen API key is unrelated and is never sent
to browsers.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Staff


COOKIE_NAME = "jx_handover_session"


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    staff_id: int | None = None


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_secret_path() -> Path:
    return config.DATA_DIR / "session_secret.key"


def _harden_secret_acl(path: Path) -> None:
    if config.APP_MODE != "server" or os.name != "nt" or not path.exists():
        return
    try:
        identity_result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        match = re.search(rb"S-\d+(?:-\d+)+", identity_result.stdout)
        grants = ["*S-1-5-18:F", "*S-1-5-32-544:F"]
        if match:
            grants.append(f"*{match.group(0).decode('ascii')}:F")
        subprocess.run(
            [
                "icacls.exe", str(path), "/inheritance:r", "/grant:r",
                *grants,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError):
        # The signed-cookie layer remains functional on unusual filesystems;
        # normal Windows server deployments store ProgramData on NTFS.
        pass


def _session_secret() -> bytes:
    if config.SESSION_SECRET:
        return config.SESSION_SECRET.encode("utf-8")
    path = _session_secret_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing.encode("utf-8")
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(generated, encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    try:
        temporary.replace(path)
    except OSError:
        # A concurrent first request may have created the final file.
        if not path.exists():
            raise
    _harden_secret_acl(path)
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


def initialize_session_secret() -> None:
    """Create the signing key before the first browsers can race to log in."""
    _session_secret()


def issue_session(identity: Identity) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "name": identity.name,
        "role": identity.role,
        "staff_id": identity.staff_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=config.SESSION_TTL_HOURS)).timestamp()),
    }
    encoded = _b64_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64_encode(
        hmac.new(_session_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def decode_session(token: str | None) -> Identity | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.split(".", 1)
    expected = _b64_encode(
        hmac.new(_session_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64_decode(encoded).decode("utf-8"))
        if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        name = str(payload.get("name") or "").strip()
        role = str(payload.get("role") or "operator").strip()
        if not name:
            return None
        staff_id = payload.get("staff_id")
        return Identity(
            name=name,
            role=role,
            staff_id=int(staff_id) if staff_id is not None else None,
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def identity_from_request(request: Request) -> Identity | None:
    return decode_session(request.cookies.get(COOKIE_NAME))


def require_identity(request: Request) -> Identity:
    identity = identity_from_request(request)
    if identity is not None:
        return identity
    if not config.AUTH_REQUIRED:
        return Identity(name="本机用户", role="admin")
    raise HTTPException(
        status_code=401,
        detail={"code": "LOGIN_REQUIRED", "message": "请选择姓名后进入系统。"},
    )


def require_admin(identity: Identity = Depends(require_identity)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(403, "仅系统管理员可以执行此操作。")
    return identity


def authenticate_staff(
    db: Session,
    *,
    name: str,
    access_code: str,
) -> Identity:
    clean_name = name.strip()
    staff = (
        db.query(Staff)
        .filter(Staff.is_active == 1, Staff.name == clean_name)
        .order_by(Staff.id)
        .first()
    )
    if staff is None:
        raise HTTPException(422, "请选择有效的人员姓名。")
    if config.ACCESS_CODE and not hmac.compare_digest(
        access_code.encode("utf-8"), config.ACCESS_CODE.encode("utf-8")
    ):
        raise HTTPException(401, "访问口令不正确。")
    role = "admin" if clean_name in config.ADMIN_NAMES else "operator"
    return Identity(name=clean_name, role=role, staff_id=staff.id)
