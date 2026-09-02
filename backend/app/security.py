"""Signed browser sessions and per-person password authentication.

Cloud mode uses one account per staff name, Argon2id password hashes and a
mandatory first-login password change.  Legacy LAN mode may keep the shared
access code until it is explicitly upgraded.  The Qwen API key is unrelated
and is never sent to browsers.
"""
from __future__ import annotations

import base64
from collections import deque
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
import threading
import time

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Staff, now_iso


COOKIE_NAME = "jx_handover_session"
MIN_NEW_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
PASSWORD_HASH = PasswordHash.recommended()
# A missing or duplicate account still performs a real Argon2id verification,
# keeping login timing close to the valid-account path.
DUMMY_PASSWORD_HASH = PASSWORD_HASH.hash(secrets.token_urlsafe(32))


class LoginAttemptLimiter:
    """Small per-client limiter suitable for the required single process."""

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: int,
        block_seconds: int,
    ) -> None:
        self.max_failures = max(1, max_failures)
        self.window_seconds = max(1, window_seconds)
        self.block_seconds = max(1, block_seconds)
        self._failures: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, client_key: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        with self._lock:
            until = self._blocked_until.get(client_key, 0.0)
            if until <= current:
                self._blocked_until.pop(client_key, None)
                return 0
            return max(1, int(until - current + 0.999))

    def record_failure(self, client_key: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures.setdefault(client_key, deque())
            cutoff = current - self.window_seconds
            while failures and failures[0] < cutoff:
                failures.popleft()
            failures.append(current)
            if len(failures) < self.max_failures:
                return 0
            self._failures.pop(client_key, None)
            blocked_until = current + self.block_seconds
            self._blocked_until[client_key] = blocked_until
            return self.block_seconds

    def record_success(self, client_key: str) -> None:
        with self._lock:
            self._failures.pop(client_key, None)
            self._blocked_until.pop(client_key, None)


LOGIN_ATTEMPTS = LoginAttemptLimiter(
    max_failures=config.LOGIN_MAX_FAILURES,
    window_seconds=config.LOGIN_WINDOW_SECONDS,
    block_seconds=config.LOGIN_BLOCK_SECONDS,
)
LOGIN_NETWORK_ATTEMPTS = LoginAttemptLimiter(
    max_failures=config.LOGIN_NETWORK_MAX_FAILURES,
    window_seconds=config.LOGIN_WINDOW_SECONDS,
    block_seconds=config.LOGIN_BLOCK_SECONDS,
)


def _login_client_key(request: Request) -> str:
    return (request.client.host if request.client else "unknown")[:100]


def assert_login_allowed(request: Request, name: str) -> tuple[str, str]:
    network_key = _login_client_key(request)
    identity_key = f"{network_key}|{name.strip().casefold()[:100]}"
    retry_after = max(
        LOGIN_ATTEMPTS.retry_after(identity_key),
        LOGIN_NETWORK_ATTEMPTS.retry_after(network_key),
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试。",
            headers={"Retry-After": str(retry_after)},
        )
    return identity_key, network_key


def record_login_failure(keys: tuple[str, str]) -> None:
    identity_key, network_key = keys
    LOGIN_ATTEMPTS.record_failure(identity_key)
    LOGIN_NETWORK_ATTEMPTS.record_failure(network_key)


def record_login_success(keys: tuple[str, str]) -> None:
    identity_key, network_key = keys
    LOGIN_ATTEMPTS.record_success(identity_key)
    LOGIN_NETWORK_ATTEMPTS.record_success(network_key)


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    staff_id: int | None = None
    session_version: int = 0
    password_change_required: bool = False


def hash_password(password: str) -> str:
    return PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(password_hash) and PASSWORD_HASH.verify(password, password_hash)
    except Exception:  # noqa: BLE001 - malformed legacy hashes must fail closed
        return False


def initialize_staff_password(staff: Staff) -> bool:
    """Initialize one account once without ever persisting the plaintext."""
    if staff.password_hash:
        return False
    staff.password_hash = hash_password(config.INITIAL_ACCOUNT_PASSWORD)
    staff.must_change_password = 1
    staff.session_version = max(1, int(staff.session_version or 0))
    staff.password_updated_at = None
    return True


def initialize_missing_staff_passwords(db: Session) -> int:
    if not config.ACCOUNT_LOGIN_ENABLED:
        return 0
    initialized = 0
    for staff in db.query(Staff).order_by(Staff.id).all():
        if initialize_staff_password(staff):
            initialized += 1
    if initialized:
        db.commit()
    return initialized


def validate_account_directory(db: Session) -> None:
    """Fail startup when a person's name cannot identify exactly one account."""
    if not config.ACCOUNT_LOGIN_ENABLED:
        return
    active_rows = (
        db.query(Staff)
        .filter(Staff.is_active == 1)
        .order_by(Staff.name, Staff.id)
        .all()
    )
    name_counts: dict[str, int] = {}
    for staff in active_rows:
        clean_name = staff.name.strip()
        if clean_name:
            name_counts[clean_name] = name_counts.get(clean_name, 0) + 1
    problems: list[str] = []
    if any(not staff.name.strip() for staff in active_rows):
        problems.append("存在姓名为空的启用人员，请先补全或停用该人员。")
    duplicates = sorted(name for name, count in name_counts.items() if count > 1)
    if duplicates:
        problems.append(
            "存在同名启用人员，姓名账号无法唯一识别：" + "、".join(duplicates)
        )
    missing_admins = sorted(config.ADMIN_NAMES - set(name_counts))
    if missing_admins:
        problems.append(
            "JX_ADMIN_NAMES 中的管理员不在启用人员名单："
            + "、".join(missing_admins)
        )
    if problems:
        raise RuntimeError("人员账号初始化未通过：\n" + "\n".join(
            f"- {problem}" for problem in problems
        ))


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
        "session_version": identity.session_version,
        "password_change_required": identity.password_change_required,
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
            session_version=int(payload.get("session_version") or 0),
            password_change_required=bool(
                payload.get("password_change_required", False)
            ),
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def identity_from_request(request: Request) -> Identity | None:
    return decode_session(request.cookies.get(COOKIE_NAME))


def _current_account_identity(
    db: Session,
    identity: Identity | None,
) -> Identity | None:
    if identity is None or not config.ACCOUNT_LOGIN_ENABLED:
        return identity
    if identity.staff_id is None:
        return None
    staff = db.get(Staff, identity.staff_id)
    if (
        staff is None
        or not staff.is_active
        or staff.name != identity.name
        or int(staff.session_version or 0) != identity.session_version
    ):
        return None
    role = "admin" if staff.name in config.ADMIN_NAMES else "operator"
    return Identity(
        name=staff.name,
        role=role,
        staff_id=staff.id,
        session_version=int(staff.session_version or 0),
        password_change_required=bool(staff.must_change_password),
    )


def validated_identity_from_request(
    request: Request,
    db: Session,
) -> Identity | None:
    return _current_account_identity(db, identity_from_request(request))


def require_session_identity(
    request: Request,
    db: Session = Depends(get_db),
) -> Identity:
    identity = validated_identity_from_request(request, db)
    if identity is not None:
        return identity
    if not config.AUTH_REQUIRED:
        return Identity(name="本机用户", role="admin")
    raise HTTPException(
        status_code=401,
        detail={"code": "LOGIN_REQUIRED", "message": "请使用姓名和个人密码登录。"},
    )


def require_identity(
    request: Request,
    db: Session = Depends(get_db),
) -> Identity:
    identity = require_session_identity(request, db)
    if identity.password_change_required:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "首次登录必须先修改初始密码。",
            },
        )
    return identity


def require_admin(identity: Identity = Depends(require_identity)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(403, "仅系统管理员可以执行此操作。")
    return identity


def authenticate_staff(
    db: Session,
    *,
    name: str,
    password: str = "",
    access_code: str = "",
) -> Identity:
    clean_name = name.strip()
    staff_rows = (
        db.query(Staff)
        .filter(Staff.is_active == 1, Staff.name == clean_name)
        .order_by(Staff.id)
        .limit(2)
        .all()
    )
    if config.ACCOUNT_LOGIN_ENABLED:
        if len(staff_rows) != 1:
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise HTTPException(401, "账号或密码不正确。")
        staff = staff_rows[0]
        if not verify_password(password, staff.password_hash):
            raise HTTPException(401, "账号或密码不正确。")
        staff.last_login_at = now_iso()
        db.commit()
    else:
        if not staff_rows:
            raise HTTPException(422, "请选择有效的人员姓名。")
        staff = staff_rows[0]
        if config.ACCESS_CODE and not hmac.compare_digest(
            access_code.encode("utf-8"), config.ACCESS_CODE.encode("utf-8")
        ):
            raise HTTPException(401, "访问口令不正确。")
    role = "admin" if clean_name in config.ADMIN_NAMES else "operator"
    return Identity(
        name=clean_name,
        role=role,
        staff_id=staff.id,
        session_version=int(staff.session_version or 0),
        password_change_required=(
            bool(staff.must_change_password)
            if config.ACCOUNT_LOGIN_ENABLED else False
        ),
    )


def change_staff_password(
    db: Session,
    *,
    identity: Identity,
    current_password: str,
    new_password: str,
) -> Identity:
    if not config.ACCOUNT_LOGIN_ENABLED or identity.staff_id is None:
        raise HTTPException(409, "当前运行模式未启用个人账号密码。")
    staff = db.get(Staff, identity.staff_id)
    if staff is None or not staff.is_active or staff.name != identity.name:
        raise HTTPException(401, "当前账号已失效，请重新登录。")
    if not verify_password(current_password, staff.password_hash):
        raise HTTPException(401, "当前密码不正确。")
    if len(new_password) < MIN_NEW_PASSWORD_LENGTH:
        raise HTTPException(422, f"新密码至少需要 {MIN_NEW_PASSWORD_LENGTH} 个字符。")
    if len(new_password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(422, f"新密码不能超过 {MAX_PASSWORD_LENGTH} 个字符。")
    if new_password == config.INITIAL_ACCOUNT_PASSWORD:
        raise HTTPException(422, "新密码不能继续使用系统初始密码。")
    if verify_password(new_password, staff.password_hash):
        raise HTTPException(422, "新密码不能与当前密码相同。")
    staff.password_hash = hash_password(new_password)
    staff.must_change_password = 0
    staff.password_updated_at = now_iso()
    staff.session_version = max(1, int(staff.session_version or 0)) + 1
    db.commit()
    role = "admin" if staff.name in config.ADMIN_NAMES else "operator"
    return Identity(
        name=staff.name,
        role=role,
        staff_id=staff.id,
        session_version=int(staff.session_version),
        password_change_required=False,
    )


def reset_staff_password(db: Session, staff: Staff) -> None:
    staff.password_hash = hash_password(config.INITIAL_ACCOUNT_PASSWORD)
    staff.must_change_password = 1
    staff.password_updated_at = None
    staff.session_version = max(1, int(staff.session_version or 0)) + 1
    db.commit()
