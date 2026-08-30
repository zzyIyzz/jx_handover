"""Global configuration shared by source runs and the Windows package."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


RUNNING_FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_BASE = Path(__file__).resolve().parents[2]
RESOURCE_BASE = Path(getattr(sys, "_MEIPASS", SOURCE_BASE)).resolve()
load_dotenv(SOURCE_BASE / ".env")

# v0.3.0 intentionally binds to localhost only and uses one fixed port so the
# controller can distinguish this service from an unrelated process.
APP_HOST = "127.0.0.1"
APP_PORT = 8765

configured_data_root = os.getenv("JX_HANDOVER_DATA_DIR", "").strip()
if configured_data_root:
    USER_DATA_ROOT = Path(configured_data_root).expanduser().resolve()
elif RUNNING_FROZEN:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    USER_DATA_ROOT = Path(local_app_data) / "JXHandover"
else:
    USER_DATA_ROOT = SOURCE_BASE / "runtime"

DATA_DIR = USER_DATA_ROOT / "data"
IMPORT_DIR = USER_DATA_ROOT / "imports"
GENERATED_DIR = USER_DATA_ROOT / "generated"
SNAPSHOT_DIR = USER_DATA_ROOT / "snapshots"
LOG_DIR = USER_DATA_ROOT / "logs"

for _directory in (DATA_DIR, IMPORT_DIR, GENERATED_DIR, SNAPSHOT_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "handover.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

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

# Optional cloud directory remains opt-in. Local snapshots are always retained.
CLOUD_PUBLISH_DIR = os.getenv("CLOUD_PUBLISH_DIR", "").strip()

# AI
AI_MODE = os.getenv("AI_MODE", "mock").strip().lower()
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "").strip()
QWEN_MODEL = os.getenv("QWEN_MODEL", "").strip()
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "").strip()
AI_STRUCTURED_MODE = os.getenv("AI_STRUCTURED_MODE", "json_schema").strip()

