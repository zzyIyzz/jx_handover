"""Global configuration shared by desktop and LAN-server packages.

V0.4 keeps port 8765 fixed, but separates two explicit operating modes:

``desktop``
    Listen on loopback and keep data in the current user's LocalAppData.
``server``
    Listen on all interfaces and keep the only live database on the server's
    local disk.  A NAS path may receive completed backups, never the live DB.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


APP_VERSION = "0.4.1"


def _env_float(name: str, default: float, *, minimum: float) -> float:
    """Read a numeric setting without making a typo prevent startup."""
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Read an integer setting with a safe, documented fallback."""
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


RUNNING_FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_BASE = Path(__file__).resolve().parents[2]
RESOURCE_BASE = Path(getattr(sys, "_MEIPASS", SOURCE_BASE)).resolve()
load_dotenv(SOURCE_BASE / ".env")

configured_env_file = os.getenv("JX_HANDOVER_CONFIG_FILE", "").strip()
if configured_env_file:
    load_dotenv(Path(configured_env_file).expanduser(), override=True)

# Port 8765 is intentionally fixed in both modes.  Server mode changes only
# the bind address; users visit the server IP or DNS name, not 0.0.0.0.
APP_MODE = os.getenv("JX_HANDOVER_MODE", "desktop").strip().lower()
if APP_MODE not in {"desktop", "server"}:
    APP_MODE = "desktop"
APP_HOST = "0.0.0.0" if APP_MODE == "server" else "127.0.0.1"
APP_PORT = 8765
PUBLIC_HOST = os.getenv("JX_PUBLIC_HOST", "").strip()
PUBLIC_URL = (
    f"http://{PUBLIC_HOST}:{APP_PORT}"
    if PUBLIC_HOST
    else (f"http://127.0.0.1:{APP_PORT}" if APP_MODE == "desktop" else "")
)

configured_data_root = os.getenv("JX_HANDOVER_DATA_DIR", "").strip()
if configured_data_root:
    USER_DATA_ROOT = Path(configured_data_root).expanduser().resolve()
elif RUNNING_FROZEN:
    if APP_MODE == "server":
        program_data = os.getenv("PROGRAMDATA", "").strip()
        if not program_data:
            program_data = str(Path.home() / "AppData" / "Local")
        USER_DATA_ROOT = Path(program_data) / "JXHandoverServer"
    else:
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if not local_app_data:
            local_app_data = str(Path.home() / "AppData" / "Local")
        USER_DATA_ROOT = Path(local_app_data) / "JXHandover"
else:
    USER_DATA_ROOT = SOURCE_BASE / (
        "runtime-server" if APP_MODE == "server" else "runtime"
    )

DATA_DIR = USER_DATA_ROOT / "data"
IMPORT_DIR = USER_DATA_ROOT / "imports"
GENERATED_DIR = USER_DATA_ROOT / "generated"
SNAPSHOT_DIR = USER_DATA_ROOT / "snapshots"
LOG_DIR = USER_DATA_ROOT / "logs"

for _directory in (DATA_DIR, IMPORT_DIR, GENERATED_DIR, SNAPSHOT_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "handover.db"
DATABASE_URL = os.getenv(
    "JX_DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}"
).strip()

WORD_TEMPLATE = Path(os.getenv(
    "WORD_TEMPLATE",
    str(RESOURCE_BASE / "backend" / "app" / "templates" / "word" / "handover_v1.docx"),
))
if not WORD_TEMPLATE.is_absolute():
    WORD_TEMPLATE = RESOURCE_BASE / WORD_TEMPLATE

STANDARD_IMPORT_TEMPLATE = (
    RESOURCE_BASE / "resources" / "交接班系统标准导入模板_V0.3.0.xlsx"
)
FRONTEND_DIST = RESOURCE_BASE / "frontend" / "dist"

# Optional NAS directory remains opt-in. Local snapshots are always retained;
# only completed backup files may be copied to this directory.
CLOUD_PUBLISH_DIR = os.getenv("CLOUD_PUBLISH_DIR", "").strip()
NAS_BACKUP_DIR = os.getenv("JX_NAS_BACKUP_DIR", "").strip()

# AI
AI_MODE = os.getenv("AI_MODE", "mock").strip().lower()
if AI_MODE not in {"mock", "qwen"}:
    AI_MODE = "mock"
QWEN_BASE_URL = os.getenv(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).strip()
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.8-flash").strip()
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "").strip()
AI_STRUCTURED_MODE = os.getenv("AI_STRUCTURED_MODE", "json_schema").strip()
AI_TIMEOUT_SECONDS = _env_float("AI_TIMEOUT_SECONDS", 60.0, minimum=5.0)

# Lightweight LAN identity.  In server mode every browser selects a staff name
# once.  A shared access code can be required without exposing the Qwen key.
AUTH_REQUIRED = os.getenv(
    "JX_AUTH_REQUIRED", "1" if APP_MODE == "server" else "0"
).strip().lower() not in {"0", "false", "no", "off"}
ACCESS_CODE = os.getenv("JX_ACCESS_CODE", "").strip()
SESSION_SECRET = os.getenv("JX_SESSION_SECRET", "").strip()
SESSION_TTL_HOURS = _env_int("JX_SESSION_TTL_HOURS", 168, minimum=1)
ADMIN_NAMES = {
    value.strip()
    for value in os.getenv("JX_ADMIN_NAMES", "").split(",")
    if value.strip()
}
