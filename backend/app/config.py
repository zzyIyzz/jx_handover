"""Global configuration shared by desktop, LAN-server and cloud packages.

Port 8765 stays fixed, but the bind address and security requirements depend
on one of three explicit operating modes:

``desktop``
    Listen on loopback and keep data in the current user's LocalAppData.
``server``
    Listen on all interfaces and keep the only live database on the server's
    local disk.  A NAS path may receive completed backups, never the live DB.
``cloud``
    Listen inside one container and require HTTPS, strict host validation,
    secure cookies and an outer private-access boundary at the reverse proxy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


APP_VERSION = os.getenv("JX_APP_VERSION", "0.5.0").strip() or "0.5.0"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


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

# Port 8765 is intentionally fixed in every mode.  Cloud mode publishes it
# only to the ECS loopback interface; BaoTa/Nginx owns public HTTPS port 443.
APP_MODE = os.getenv("JX_HANDOVER_MODE", "desktop").strip().lower()
if APP_MODE not in {"desktop", "server", "cloud"}:
    APP_MODE = "desktop"
APP_HOST = "0.0.0.0" if APP_MODE in {"server", "cloud"} else "127.0.0.1"
APP_PORT = 8765
PUBLIC_HOST = os.getenv("JX_PUBLIC_HOST", "").strip()
_explicit_public_url = os.getenv("JX_PUBLIC_URL", "").strip().rstrip("/")
PUBLIC_URL = _explicit_public_url or (
    f"http://{PUBLIC_HOST}:{APP_PORT}" if PUBLIC_HOST
    else (f"http://127.0.0.1:{APP_PORT}" if APP_MODE == "desktop" else "")
)
_public_url_parts = urlsplit(PUBLIC_URL)
PUBLIC_HOSTNAME = (_public_url_parts.hostname or PUBLIC_HOST).strip().lower()

_trusted_hosts_raw = os.getenv("JX_TRUSTED_HOSTS", "").strip()
TRUSTED_HOSTS = [
    value.strip().lower()
    for value in _trusted_hosts_raw.split(",")
    if value.strip()
]
if not TRUSTED_HOSTS and APP_MODE == "desktop":
    TRUSTED_HOSTS = ["127.0.0.1", "localhost"]

CLOUD_ACCESS_SCOPE = os.getenv("JX_CLOUD_ACCESS_SCOPE", "").strip().lower()
COOKIE_SECURE = _env_bool("JX_COOKIE_SECURE", APP_MODE == "cloud")

configured_data_root = os.getenv("JX_HANDOVER_DATA_DIR", "").strip()
if configured_data_root:
    USER_DATA_ROOT = Path(configured_data_root).expanduser().resolve()
elif RUNNING_FROZEN:
    if APP_MODE in {"server", "cloud"}:
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
        "runtime-server" if APP_MODE in {"server", "cloud"} else "runtime"
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

# Lightweight shared identity.  Cloud mode is deliberately allowed only behind
# a private reverse-proxy boundary (fixed office IPs or a VPN); it is not an
# Internet identity provider and must never be opened to arbitrary source IPs.
AUTH_REQUIRED = os.getenv(
    "JX_AUTH_REQUIRED", "1" if APP_MODE in {"server", "cloud"} else "0"
).strip().lower() not in {"0", "false", "no", "off"}
ACCESS_CODE = os.getenv("JX_ACCESS_CODE", "").strip()
SESSION_SECRET = os.getenv("JX_SESSION_SECRET", "").strip()
SESSION_TTL_HOURS = _env_int(
    "JX_SESSION_TTL_HOURS", 12 if APP_MODE == "cloud" else 168, minimum=1
)
LOGIN_MAX_FAILURES = _env_int("JX_LOGIN_MAX_FAILURES", 5, minimum=1)
LOGIN_NETWORK_MAX_FAILURES = _env_int(
    "JX_LOGIN_NETWORK_MAX_FAILURES", 30, minimum=LOGIN_MAX_FAILURES
)
LOGIN_WINDOW_SECONDS = _env_int("JX_LOGIN_WINDOW_SECONDS", 600, minimum=30)
LOGIN_BLOCK_SECONDS = _env_int("JX_LOGIN_BLOCK_SECONDS", 900, minimum=30)
ADMIN_NAMES = {
    value.strip()
    for value in os.getenv("JX_ADMIN_NAMES", "").split(",")
    if value.strip()
}


def validate_runtime_configuration() -> None:
    """Fail closed when a cloud deployment would expose an unsafe setup.

    The Docker entrypoint and FastAPI startup both call this function.  Keeping
    validation in the application prevents a later Compose or BaoTa edit from
    silently disabling the controls described in the deployment guide.
    """
    if APP_MODE != "cloud":
        return

    problems: list[str] = []
    parsed = urlsplit(PUBLIC_URL)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        problems.append("JX_PUBLIC_URL 必须是无路径的 HTTPS 域名，例如 https://handover.example.com。")
    if not AUTH_REQUIRED:
        problems.append("JX_AUTH_REQUIRED 必须为 1。")
    if len(ACCESS_CODE) < 12:
        problems.append("JX_ACCESS_CODE 至少需要 12 个字符。")
    elif any(marker in ACCESS_CODE for marker in ("请替换", "请填写")):
        problems.append("JX_ACCESS_CODE 仍是示例占位文字，请生成真实随机口令。")
    if len(SESSION_SECRET) < 32:
        problems.append("JX_SESSION_SECRET 至少需要 32 个字符，并且只能保存在服务器配置文件中。")
    elif any(marker in SESSION_SECRET for marker in ("请替换", "请填写")):
        problems.append("JX_SESSION_SECRET 仍是示例占位文字，请生成真实随机密钥。")
    if not COOKIE_SECURE:
        problems.append("JX_COOKIE_SECURE 必须为 1。")
    if SESSION_TTL_HOURS > 24:
        problems.append("云端 JX_SESSION_TTL_HOURS 不能超过 24 小时。")
    if not ADMIN_NAMES:
        problems.append("JX_ADMIN_NAMES 至少需要配置一名系统管理员。")
    elif any(marker in name for name in ADMIN_NAMES for marker in ("请替换", "请填写")):
        problems.append("JX_ADMIN_NAMES 仍是示例占位文字，请填写实际管理员姓名。")
    if CLOUD_ACCESS_SCOPE != "private":
        problems.append(
            "JX_CLOUD_ACCESS_SCOPE 必须为 private，并在宝塔 Nginx 使用固定 IP 白名单或 VPN。"
        )
    if not TRUSTED_HOSTS:
        problems.append("JX_TRUSTED_HOSTS 不能为空。")
    elif "*" in TRUSTED_HOSTS:
        problems.append("云端 JX_TRUSTED_HOSTS 不允许使用通配符 *。")
    if parsed.hostname and parsed.hostname.lower() not in TRUSTED_HOSTS:
        problems.append("JX_TRUSTED_HOSTS 必须包含 JX_PUBLIC_URL 的域名。")
    if parsed.hostname and parsed.hostname.lower() == "handover.example.com":
        problems.append("JX_PUBLIC_URL 仍是示例域名，请替换为实际 HTTPS 域名。")
    if Path(USER_DATA_ROOT).resolve() == Path(Path(USER_DATA_ROOT).anchor):
        problems.append("JX_HANDOVER_DATA_DIR 不能使用文件系统根目录。")
    if not DATABASE_URL.startswith("sqlite:///"):
        problems.append("当前云端测试版只支持 ECS 本地磁盘上的单实例 SQLite 数据库。")

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in problems)
        raise RuntimeError(f"云端安全配置未通过：\n{formatted}")
