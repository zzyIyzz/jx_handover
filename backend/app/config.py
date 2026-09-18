"""Global configuration shared by desktop, LAN-server and cloud packages.

The application keeps its existing port 8765.  In cloud mode that port is
published only on ECS loopback, while BaoTa/Nginx owns the user-selected
public HTTPS port 1215.  Bind address and security requirements depend on one
of three explicit modes:

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
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


FALLBACK_APP_VERSION = "0.5.3"


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


def _read_version_file() -> str:
    """Read the single version source shared by the app and the controller.

    ``server_config.py`` used to hard-code its own number, so a packaged server
    could report V0.4.1 while the running code was already newer and the
    management page showed two different versions.
    """
    for base in (RESOURCE_BASE, SOURCE_BASE):
        try:
            text = (base / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text.splitlines()[0].strip()
    return FALLBACK_APP_VERSION


APP_VERSION = os.getenv("JX_APP_VERSION", "").strip() or _read_version_file()

configured_env_file = os.getenv("JX_HANDOVER_CONFIG_FILE", "").strip()
if configured_env_file:
    load_dotenv(Path(configured_env_file).expanduser(), override=True)

# Preserve the application port 8765 in every mode.  Cloud Docker publishes it
# only on ECS loopback; BaoTa/Nginx separately owns public HTTPS port 1215.
APP_MODE = os.getenv("JX_HANDOVER_MODE", "desktop").strip().lower()
if APP_MODE not in {"desktop", "server", "cloud"}:
    APP_MODE = "desktop"
APP_HOST = "0.0.0.0" if APP_MODE in {"server", "cloud"} else "127.0.0.1"
APP_PORT = 8765
PUBLIC_PORT = 1215 if APP_MODE == "cloud" else APP_PORT
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
DATA_ROOT_EXPLICIT = bool(configured_data_root)
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
    # One directory for every source-run mode.  Older builds switched between
    # ``runtime`` and ``runtime-server`` based on JX_HANDOVER_MODE, so starting
    # the same checkout in another mode pointed at an empty folder and looked
    # exactly like "all accounts and handover records were lost".
    USER_DATA_ROOT = SOURCE_BASE / "runtime"

# Historical roots that may still hold the only real database.  They are only
# ever read and copied from; startup never deletes or moves anything.
LEGACY_DATA_ROOT_CANDIDATES: tuple[Path, ...] = () if RUNNING_FROZEN else (
    SOURCE_BASE / "runtime-server",
    SOURCE_BASE / "backend" / "runtime",
    SOURCE_BASE / "backend" / "runtime-server",
)
DATA_ROOT_AUTOFIND = _env_bool("JX_DATA_ROOT_AUTOFIND", True)

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

# In-process verified full backups: scheduler cadence and local retention.
# Daily backups are created at most once per calendar day; the scheduler keeps
# checking long-running processes so restarts are never required.  NAS/off-site
# copies are never pruned automatically, keeping off-site retention longer.
AUTO_BACKUP_CHECK_SECONDS = _env_int(
    "JX_AUTO_BACKUP_CHECK_SECONDS", 30 * 60, minimum=60
)
BACKUP_KEEP_DAILY = _env_int("JX_BACKUP_KEEP_DAILY", 30, minimum=1)
BACKUP_KEEP_MANUAL = _env_int("JX_BACKUP_KEEP_MANUAL", 30, minimum=1)

# AI
# ``auto`` is the default: use Qwen whenever a key is present and fall back to
# the deterministic local rules otherwise.  Requiring operators to keep
# AI_MODE=qwen in sync with the key is what silently switched a deployed server
# back to local rules and made the work-log AI look like it had disappeared.
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
AI_MODE_REQUESTED = os.getenv("AI_MODE", "auto").strip().lower()
if AI_MODE_REQUESTED not in {"auto", "mock", "qwen"}:
    AI_MODE_REQUESTED = "auto"
QWEN_BASE_URL = (
    os.getenv("QWEN_BASE_URL", "").strip() or DEFAULT_QWEN_BASE_URL
)
QWEN_MODEL = os.getenv("QWEN_MODEL", "").strip() or "qwen3.8-flash"
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "").strip()
AI_STRUCTURED_MODE = os.getenv("AI_STRUCTURED_MODE", "json_schema").strip()
AI_TIMEOUT_SECONDS = _env_float("AI_TIMEOUT_SECONDS", 60.0, minimum=5.0)


def _resolve_ai_mode() -> str:
    if AI_MODE_REQUESTED == "mock":
        return "mock"
    if AI_MODE_REQUESTED == "qwen":
        return "qwen"
    return "qwen" if QWEN_API_KEY else "mock"


# Effective mode.  Callers keep reading ``config.AI_MODE``; the requested value
# stays available so the management page can explain why AI is off.
AI_MODE = _resolve_ai_mode()


def ai_unavailable_reason() -> str:
    """Explain in one sentence why work-log AI is not running."""
    if AI_MODE == "qwen":
        return ""
    if AI_MODE_REQUESTED == "mock":
        return "服务器配置 AI_MODE=mock，已显式关闭 AI 智能整理。"
    if not QWEN_API_KEY:
        return "服务器尚未填写 QWEN_API_KEY，AI 智能整理未启用。"
    return "AI 配置不完整，当前使用本地确定性规则。"


# Server and cloud modes use one password per staff name and force a password
# change on first login.  Both are still deployed behind a private boundary
# (fixed office IPs, VPN or HTTPS reverse proxy); login replaces neither.
AUTH_REQUIRED = os.getenv(
    "JX_AUTH_REQUIRED", "1" if APP_MODE in {"server", "cloud"} else "0"
).strip().lower() not in {"0", "false", "no", "off"}
ACCOUNT_LOGIN_ENABLED = _env_bool(
    "JX_ACCOUNT_LOGIN_ENABLED", APP_MODE in {"server", "cloud"}
)
INITIAL_ACCOUNT_PASSWORD = os.getenv(
    "JX_INITIAL_ACCOUNT_PASSWORD", "aaaa0000*"
)
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

# Recovery roster, authoritative when JX_ADMIN_NAMES is empty.  Startup sync
# promotes these names AND reclaims the right from stored administrators
# outside the roster, so a deployment converges on exactly the configured
# administrators without anyone editing the database by hand.  Every
# /api/admin route is guarded, including the screen that grants the right, so
# a roster with no administrator is a roster nobody can repair from the web
# UI.  Set JX_DEFAULT_ADMIN_NAMES to an empty value to disable it.
DEFAULT_ADMIN_NAMES = {
    value.strip()
    for value in os.getenv("JX_DEFAULT_ADMIN_NAMES", "周智源,刘学森").split(",")
    if value.strip()
}


def is_admin_name(name: str) -> bool:
    """True when the server configuration pins this name as an administrator.

    The effective roster is ``JX_ADMIN_NAMES`` when set, otherwise the recovery
    roster, so the UI guards below cover the pinned administrator even on a
    server whose ``.env`` never named one.
    """
    return str(name or "").strip() in (ADMIN_NAMES or DEFAULT_ADMIN_NAMES)


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
        problems.append(
            "JX_PUBLIC_URL 必须是无路径且显式包含 :1215 的 HTTPS 地址，例如 "
            "https://handover.example.com:1215 或 https://公网IPv4:1215。"
        )
    try:
        configured_public_port = parsed.port
    except ValueError:
        configured_public_port = None
    if configured_public_port != PUBLIC_PORT:
        problems.append(
            f"云端 JX_PUBLIC_URL 必须显式包含公网 HTTPS 端口 :{PUBLIC_PORT}。"
        )
    if not AUTH_REQUIRED:
        problems.append("JX_AUTH_REQUIRED 必须为 1。")
    if not ACCOUNT_LOGIN_ENABLED:
        problems.append("云端 JX_ACCOUNT_LOGIN_ENABLED 必须为 1，使用个人账号和密码登录。")
    if ACCOUNT_LOGIN_ENABLED:
        if len(INITIAL_ACCOUNT_PASSWORD) < 8:
            problems.append("JX_INITIAL_ACCOUNT_PASSWORD 至少需要 8 个字符。")
    else:
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
    # A public entry point must name its administrators explicitly; the
    # recovery default is only a safety net for LAN and desktop installs.
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
    if parsed.hostname:
        try:
            public_address = ip_address(parsed.hostname)
        except ValueError:
            public_address = None
        if public_address is not None and (
            public_address.version != 4 or not public_address.is_global
        ):
            problems.append(
                "使用 IP 访问时，JX_PUBLIC_URL 必须填写 ECS 的真实公网 IPv4，不能使用示例、内网或保留地址。"
            )
    if Path(USER_DATA_ROOT).resolve() == Path(Path(USER_DATA_ROOT).anchor):
        problems.append("JX_HANDOVER_DATA_DIR 不能使用文件系统根目录。")
    if not DATABASE_URL.startswith("sqlite:///"):
        problems.append("当前云端测试版只支持 ECS 本地磁盘上的单实例 SQLite 数据库。")

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in problems)
        raise RuntimeError(f"云端安全配置未通过：\n{formatted}")
