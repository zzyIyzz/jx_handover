"""Machine-local settings for the V0.4.1 Windows LAN server package."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Any
import uuid


APP_VERSION = "0.4.1"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.8-flash"


def server_root() -> Path:
    base = os.getenv("PROGRAMDATA", "").strip()
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    override = os.getenv("JX_HANDOVER_SERVER_HOME", "").strip()
    return Path(override or base) / ("" if override else "JXHandoverServer")


ROOT = server_root().resolve()
SETTINGS_PATH = ROOT / "server-settings.json"
SECRETS_PATH = ROOT / "server-secrets.bin"
STOP_REQUEST_PATH = ROOT / "control" / "stop.request"
PID_PATH = ROOT / "control" / "server.pid"
ERROR_PATH = ROOT / "control" / "server-error.txt"
RUNNER_LOG_PATH = ROOT / "logs" / "server-runner.log"


DEFAULT_SETTINGS: dict[str, Any] = {
    "public_host": "",
    "data_root": "",
    "qwen_base_url": DEFAULT_QWEN_BASE_URL,
    "qwen_model": DEFAULT_MODEL,
    "admin_names": "",
    "nas_backup_dir": "",
    "auto_open_browser": True,
    "auth_required": True,
}
DEFAULT_SECRETS = {"qwen_api_key": "", "access_code": ""}


def configured_data_root(settings: dict[str, Any] | None = None) -> Path:
    """Return the selected live-data root, defaulting to local ProgramData.

    Controller settings and encrypted secrets deliberately remain under
    ``ROOT``.  This keeps one stable bootstrap location even when an
    administrator chooses another local fixed disk for the potentially much
    larger database, imports and generated Word files.
    """
    raw = str((settings or {}).get("data_root") or "").strip()
    return Path(raw).expanduser().resolve() if raw else ROOT


def _looks_like_unc(path_text: str) -> bool:
    normalized = path_text.strip().replace("/", "\\")
    return normalized.startswith("\\\\")


def _windows_drive_type(path: Path) -> int | None:
    if os.name != "nt":
        return None
    anchor = path.anchor
    if not anchor:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    return int(kernel32.GetDriveTypeW(anchor))


def validate_local_data_root(value: str | Path, *, create: bool = True) -> Path:
    """Validate that the live SQLite root is on this server's fixed disk.

    UNC paths and mapped network drives are rejected even when they are
    currently reachable.  A short successful test cannot prove that SMB file
    locking and sync semantics will remain safe during a later disconnect.
    """
    raw = str(value).strip()
    if not raw:
        raise ValueError("请填写服务器本地正式数据目录。")
    if _looks_like_unc(raw):
        raise ValueError(
            "正式数据库不能放在 \\\\服务器\\共享目录。请为正式数据选择服务器本地磁盘，"
            "并把共享盘填写到“NAS/云盘备份目录”。"
        )
    path = Path(raw).expanduser().resolve()
    if path.anchor and path == Path(path.anchor):
        raise ValueError(
            "不能把整个磁盘根目录作为正式数据目录。请新建专用目录，例如 "
            "D:\\JXHandoverData。"
        )
    drive_type = _windows_drive_type(path)
    # DRIVE_FIXED=3, DRIVE_REMOTE=4.  Reject all non-fixed media for an
    # unattended shared service, including a mapped SMB drive.
    if drive_type is not None and drive_type != 3:
        detail = "映射网络盘" if drive_type == 4 else "非固定本地磁盘"
        raise ValueError(
            f"所选正式数据目录位于{detail}，不能承载运行中的 SQLite 数据库。"
            "请选择服务器本地固定磁盘，例如 C:\\ProgramData\\JXHandoverServer "
            "或 D:\\JXHandoverData。"
        )
    if create:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".jx-{uuid.uuid4().hex[:8]}.tmp"
        try:
            with probe.open("xb") as stream:
                stream.write(b"JXHandover local data directory probe\n")
                stream.flush()
                os.fsync(stream.fileno())
            if not probe.is_file() or probe.stat().st_size == 0:
                raise OSError("写入测试文件后无法读取。")
        finally:
            probe.unlink(missing_ok=True)
    return path


def detect_public_hosts() -> list[str]:
    """Return usable local IPv4 candidates in deterministic order."""
    candidates: set[str] = set()
    try:
        for result in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        ):
            candidates.add(str(result[4][0]))
    except OSError:
        pass
    try:
        candidates.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(
        address
        for address in candidates
        if address and not address.startswith(("127.", "169.254."))
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(payload: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(payload)
    return _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _windows_crypto_functions():
    """Return 64-bit-safe DPAPI functions with explicit ctypes signatures."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _protect(payload: bytes) -> bytes:
    if os.name != "nt":
        return b"JX-DEV-PLAIN\0" + base64.b64encode(payload)
    crypt32, kernel32 = _windows_crypto_functions()
    source, source_buffer = _blob(payload)
    output = _DataBlob()
    # CRYPTPROTECT_LOCAL_MACHINE: the headless startup task and controller can
    # decrypt on this server, while copying the file elsewhere is useless.
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "JXHandoverServer",
        None,
        None,
        None,
        0x5,  # LOCAL_MACHINE | UI_FORBIDDEN
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _unprotect(payload: bytes) -> bytes:
    if payload.startswith(b"JX-DEV-PLAIN\0"):
        return base64.b64decode(payload.split(b"\0", 1)[1])
    if os.name != "nt":
        raise RuntimeError("Encrypted Windows server secrets cannot be read here.")
    crypt32, kernel32 = _windows_crypto_functions()
    source, source_buffer = _blob(payload)
    output = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def detect_public_host() -> str:
    candidates = detect_public_hosts()
    return candidates[0] if candidates else ""


def load_server_settings() -> tuple[dict[str, Any], dict[str, str]]:
    settings = dict(DEFAULT_SETTINGS)
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings.update({key: loaded[key] for key in DEFAULT_SETTINGS if key in loaded})
    except (OSError, ValueError, TypeError):
        pass
    if not str(settings.get("public_host") or "").strip():
        settings["public_host"] = detect_public_host()

    secrets_value = dict(DEFAULT_SECRETS)
    try:
        decrypted = json.loads(_unprotect(SECRETS_PATH.read_bytes()).decode("utf-8"))
        if isinstance(decrypted, dict):
            secrets_value.update({
                key: str(decrypted.get(key) or "")
                for key in DEFAULT_SECRETS
            })
    except (OSError, ValueError, TypeError, RuntimeError):
        pass
    return settings, secrets_value


def save_server_settings(settings: dict[str, Any], secrets_value: dict[str, str]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    clean_settings = {
        key: settings.get(key, DEFAULT_SETTINGS[key])
        for key in DEFAULT_SETTINGS
    }
    clean_settings["qwen_model"] = DEFAULT_MODEL
    settings_tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    settings_tmp.write_text(
        json.dumps(clean_settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    settings_tmp.replace(SETTINGS_PATH)

    clean_secrets = {
        key: str(secrets_value.get(key) or "")
        for key in DEFAULT_SECRETS
    }
    encrypted = _protect(
        json.dumps(clean_secrets, ensure_ascii=False).encode("utf-8")
    )
    secrets_tmp = SECRETS_PATH.with_suffix(".bin.tmp")
    secrets_tmp.write_bytes(encrypted)
    secrets_tmp.replace(SECRETS_PATH)
    _harden_secret_acl(SECRETS_PATH)


def _harden_secret_acl(path: Path) -> None:
    """Limit a machine-DPAPI blob to SYSTEM and local Administrators.

    ``CRYPTPROTECT_LOCAL_MACHINE`` is required because the interactive
    controller and a SYSTEM scheduled task are different Windows identities.
    Machine scope therefore relies on this NTFS ACL as its second boundary.
    Failure is non-fatal on non-NTFS development drives; deployment guidance
    tells administrators to keep ProgramData on NTFS.
    """
    if os.name != "nt" or not path.exists():
        return
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        identity_result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            creationflags=creation_flags,
        )
        match = re.search(rb"S-\d+(?:-\d+)+", identity_result.stdout)
        grants = ["*S-1-5-18:F", "*S-1-5-32-544:F"]
        if match:
            grants.append(f"*{match.group(0).decode('ascii')}:F")
        subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                *grants,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.CalledProcessError):
        # Settings are still protected by DPAPI.  The controller reports the
        # deployment requirement; startup should not be made unavailable only
        # because an unusual filesystem cannot apply Windows ACLs.
        pass


def apply_server_environment(
    settings: dict[str, Any] | None = None,
    secrets_value: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    if settings is None or secrets_value is None:
        settings, secrets_value = load_server_settings()
    data_root = validate_local_data_root(configured_data_root(settings), create=True)
    # Keep the historical V0.4.1 Windows package self-consistent when this
    # branch also contains the separate V0.5.x cloud deployment profile.
    os.environ["JX_APP_VERSION"] = APP_VERSION
    os.environ["JX_HANDOVER_MODE"] = "server"
    os.environ["JX_HANDOVER_DATA_DIR"] = str(data_root)
    os.environ["JX_PUBLIC_HOST"] = str(settings.get("public_host") or "").strip()
    os.environ["JX_NAS_BACKUP_DIR"] = str(settings.get("nas_backup_dir") or "").strip()
    os.environ["JX_AUTH_REQUIRED"] = "1" if settings.get("auth_required", True) else "0"
    os.environ["JX_ADMIN_NAMES"] = str(settings.get("admin_names") or "").strip()
    os.environ["JX_ACCESS_CODE"] = str(secrets_value.get("access_code") or "")
    os.environ["AI_MODE"] = "qwen"
    os.environ["QWEN_MODEL"] = DEFAULT_MODEL
    os.environ["QWEN_BASE_URL"] = str(
        settings.get("qwen_base_url") or DEFAULT_QWEN_BASE_URL
    ).strip()
    os.environ["QWEN_API_KEY"] = str(secrets_value.get("qwen_api_key") or "")
    return settings, secrets_value


def public_url(settings: dict[str, Any]) -> str:
    host = str(settings.get("public_host") or "").strip()
    return f"http://{host}:8765" if host else ""
