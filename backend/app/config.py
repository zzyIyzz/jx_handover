"""全局配置：所有路径与运行参数。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]  # jx-handover/
load_dotenv(BASE_DIR / ".env")

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8080"))

DATA_DIR = BASE_DIR / "runtime" / "data"
IMPORT_DIR = BASE_DIR / "runtime" / "imports"
GENERATED_DIR = BASE_DIR / "runtime" / "generated"
SNAPSHOT_DIR = BASE_DIR / "runtime" / "snapshots"

for _d in (DATA_DIR, IMPORT_DIR, GENERATED_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'handover.db').as_posix()}"

WORD_TEMPLATE = Path(
    os.getenv(
        "WORD_TEMPLATE",
        str(BASE_DIR / "backend" / "app" / "templates" / "word" / "handover_v1.docx"),
    )
)
if not WORD_TEMPLATE.is_absolute():
    WORD_TEMPLATE = BASE_DIR / WORD_TEMPLATE

# 正式发布目录（云盘同步目录）；留空则只输出到 GENERATED_DIR
CLOUD_PUBLISH_DIR = os.getenv("CLOUD_PUBLISH_DIR", "").strip()

# AI
AI_MODE = os.getenv("AI_MODE", "mock").strip().lower()  # mock | qwen
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "").strip()
QWEN_MODEL = os.getenv("QWEN_MODEL", "").strip()
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "").strip()
AI_STRUCTURED_MODE = os.getenv("AI_STRUCTURED_MODE", "json_schema").strip()

FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
