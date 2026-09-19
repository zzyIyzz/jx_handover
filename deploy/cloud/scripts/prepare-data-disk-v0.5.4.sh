#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Non-destructive systemd migration to the dedicated ESSD. This script never
# formats or mounts a block device; the operator must verify that first.
PROJECT="${PROJECT:-/www/wwwroot/jx_handover}"
SERVICE="${SERVICE:-jx-handover}"
PORT="${PORT:-8765}"
DATA_DEVICE="${DATA_DEVICE:-}"
DATA_MOUNT="${DATA_MOUNT:-/data}"
DATA_ROOT="${DATA_ROOT:-/data/jx-handover/data}"
PERSISTED_ENV_FILE="${PERSISTED_ENV_FILE:-/data/jx-handover/config/jx-handover.env}"
ALLOW_SYSTEM_DISK="${ALLOW_SYSTEM_DISK:-1}"
VENV="$PROJECT/.venv"
ENV_FILE="$PROJECT/.env"

SERVICE_WAS_ACTIVE=0
MIGRATION_COMPLETE=0
ENV_BACKUP=""

success() { echo "[成功] $1"; }
warn() { echo "[警告] $1"; }
error() { echo "[错误] $1" >&2; }

read_env_value() {
    local file="$1"
    local key="$2"
    [ -f "$file" ] || return 1
    awk -v wanted="$key" '
        $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
            value=$0
            sub("^[[:space:]]*" wanted "[[:space:]]*=[[:space:]]*", "", value)
            sub(/\r$/, "", value)
            if (value ~ /^".*"$/ || value ~ /^\047.*\047$/) {
                value=substr(value, 2, length(value)-2)
            }
            found=value
        }
        END { if (found != "") print found; else exit 1 }
    ' "$file"
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    local temporary=""
    temporary="$(mktemp "${file}.tmp.XXXXXX")"
    JX_ENV_REPLACEMENT="$value" awk -v wanted="$key" '
        BEGIN { done=0; replacement=ENVIRON["JX_ENV_REPLACEMENT"] }
        $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
            if (!done) print wanted "=" replacement
            done=1
            next
        }
        { print }
        END { if (!done) print wanted "=" replacement }
    ' "$file" > "$temporary"
    chmod 600 "$temporary"
    chown --reference="$file" "$temporary" 2>/dev/null || true
    mv -f -- "$temporary" "$file"
}

validate_mount() {
    local mount_source=""
    local mount_target=""
    local actual_device=""
    local expected_device=""
    local root_source=""
    local root_device=""
    mkdir -p -- "$DATA_MOUNT"
    mount_source="$(findmnt -n -o SOURCE --target "$DATA_MOUNT" 2>/dev/null || true)"
    mount_target="$(findmnt -n -o TARGET --target "$DATA_MOUNT" 2>/dev/null || true)"
    mount_source="${mount_source%%[*}"
    actual_device="$(readlink -f "$mount_source" 2>/dev/null || true)"
    [ -n "$actual_device" ] && [ -b "$actual_device" ] || {
        error "无法识别 $DATA_MOUNT 对应的块设备。"
        return 1
    }
    root_source="$(findmnt -n -o SOURCE --target / 2>/dev/null || true)"
    root_source="${root_source%%[*}"
    root_device="$(readlink -f "$root_source" 2>/dev/null || true)"
    if [ "$actual_device" = "$root_device" ]; then
        [ "$ALLOW_SYSTEM_DISK" = "1" ] || {
            error "$DATA_MOUNT 使用系统根分区；当前配置要求独立数据盘。"
            return 1
        }
        warn "将使用系统盘目录 $DATA_ROOT；系统盘故障时需依赖 OSS/外部备份恢复。"
    else
        [ "$mount_target" = "$DATA_MOUNT" ] || {
            error "$DATA_MOUNT 位于其他挂载层级，无法确认数据盘边界。"
            return 1
        }
    fi
    if [ -n "$DATA_DEVICE" ]; then
        expected_device="$(readlink -f "$DATA_DEVICE" 2>/dev/null || true)"
        [ "$actual_device" = "$expected_device" ] || {
            error "$DATA_MOUNT 来源为 $actual_device，不是指定设备 $DATA_DEVICE。"
            return 1
        }
    else
        DATA_DEVICE="$actual_device"
    fi
    [ -w "$DATA_MOUNT" ] || {
        error "挂载点不可写：$DATA_MOUNT"
        return 1
    }
    success "数据盘挂载确认：$DATA_DEVICE -> $DATA_MOUNT"
}

check_database() {
    local database="$1"
    [ -f "$database" ] || return 0
    JX_CHECK_DATABASE="$database" "$VENV/bin/python" - <<'PY'
import os
import sqlite3

database = os.environ["JX_CHECK_DATABASE"]
connection = sqlite3.connect(database, timeout=30)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
finally:
    connection.close()
if not result or str(result[0]).lower() != "ok":
    raise SystemExit("SQLite quick_check 未通过")
print("SQLite quick_check: ok")
PY
}

checkpoint_database() {
    local database="$1"
    [ -f "$database" ] || return 0
    JX_CHECK_DATABASE="$database" "$VENV/bin/python" - <<'PY'
import os
import sqlite3

connection = sqlite3.connect(os.environ["JX_CHECK_DATABASE"], timeout=30)
try:
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    result = connection.execute("PRAGMA quick_check").fetchone()
finally:
    connection.close()
if not result or str(result[0]).lower() != "ok":
    raise SystemExit("源数据库 quick_check 未通过")
PY
}

configure_environment() {
    set_env_value "$ENV_FILE" JX_HANDOVER_MODE server
    set_env_value "$ENV_FILE" JX_HANDOVER_DATA_DIR "$DATA_ROOT"
    set_env_value "$ENV_FILE" JX_ADMIN_NAMES "周智源"
    set_env_value "$ENV_FILE" JX_DEFAULT_ADMIN_NAMES "周智源"

    local session_secret=""
    session_secret="$(read_env_value "$ENV_FILE" JX_SESSION_SECRET 2>/dev/null || true)"
    if [ "${#session_secret}" -lt 32 ] || [[ "$session_secret" == *"请替换"* ]]; then
        session_secret="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
        )"
        set_env_value "$ENV_FILE" JX_SESSION_SECRET "$session_secret"
        warn "会话密钥原本缺失或无效，已生成新密钥；现有浏览器需重新登录。"
    fi
    chmod 600 "$ENV_FILE"

    local config_dir=""
    local temporary=""
    config_dir="$(dirname "$PERSISTED_ENV_FILE")"
    mkdir -p -- "$config_dir"
    chmod 700 "$config_dir"
    temporary="$(mktemp "$config_dir/.jx-env.XXXXXX")"
    cp -- "$ENV_FILE" "$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$PERSISTED_ENV_FILE"
    success "正式配置已保存到数据盘：$PERSISTED_ENV_FILE"
}

on_exit() {
    local exit_code=$?
    trap - EXIT ERR INT TERM
    if [ "$exit_code" -ne 0 ] && [ "$MIGRATION_COMPLETE" != "1" ]; then
        if [ -n "$ENV_BACKUP" ] && [ -f "$ENV_BACKUP" ]; then
            cp -a -- "$ENV_BACKUP" "$ENV_FILE" || true
        fi
        if [ "$SERVICE_WAS_ACTIVE" = "1" ]; then
            systemctl start "$SERVICE" || true
        fi
        error "数据盘迁移未完成；旧数据目录没有删除。"
    fi
    [ -z "$ENV_BACKUP" ] || rm -f -- "$ENV_BACKUP" || true
    exit "$exit_code"
}
trap on_exit EXIT
trap 'exit 130' INT TERM

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    error "请使用 root 权限执行：sudo bash $0"
    exit 1
fi
for command_name in find findmnt readlink rsync systemctl curl python3; do
    command -v "$command_name" >/dev/null 2>&1 || {
        error "缺少命令：$command_name"
        exit 1
    }
done
validate_mount
[ -d "$PROJECT/.git" ] || { error "项目目录无效：$PROJECT"; exit 1; }
[ -x "$VENV/bin/python" ] || { error "虚拟环境不存在：$VENV"; exit 1; }
[ -f "$ENV_FILE" ] || { error "正式 .env 不存在：$ENV_FILE"; exit 1; }

CURRENT_ROOT="$(cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config
print(config.USER_DATA_ROOT.resolve())
PY
)"
CURRENT_ROOT="$(readlink -f "$CURRENT_ROOT")"
TARGET_ROOT="$(readlink -m "$DATA_ROOT")"
echo "当前数据根：$CURRENT_ROOT"
echo "目标数据根：$TARGET_ROOT"

ENV_BACKUP="$(mktemp /tmp/jx-handover-env-before-data-migration.XXXXXX)"
cp -a -- "$ENV_FILE" "$ENV_BACKUP"
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    SERVICE_WAS_ACTIVE=1
    systemctl stop "$SERVICE"
fi
if systemctl is-active --quiet "$SERVICE"; then
    error "服务停止失败，拒绝复制 SQLite 数据。"
    exit 1
fi

if [ "$CURRENT_ROOT" != "$TARGET_ROOT" ]; then
    [ -d "$CURRENT_ROOT" ] || { error "当前数据根不存在：$CURRENT_ROOT"; exit 1; }
    checkpoint_database "$CURRENT_ROOT/data/handover.db"
    mkdir -p -- "$(dirname "$TARGET_ROOT")"

    if [ -e "$TARGET_ROOT" ] && [ -n "$(find "$TARGET_ROOT" -mindepth 1 -print -quit 2>/dev/null)" ]; then
        differences="$(rsync -a --checksum --delete --dry-run --itemize-changes \
            "$CURRENT_ROOT/" "$TARGET_ROOT/" || true)"
        [ -z "$differences" ] || {
            error "目标目录已有不同数据，拒绝覆盖：$TARGET_ROOT"
            exit 1
        }
        check_database "$TARGET_ROOT/data/handover.db"
        success "目标目录已有与源目录一致的数据，直接复用"
    else
        [ ! -e "$TARGET_ROOT" ] || rmdir "$TARGET_ROOT"
        staging="$(dirname "$TARGET_ROOT")/.migrate-$(date +%Y%m%d_%H%M%S)-$$"
        mkdir "$staging"
        rsync -a --checksum "$CURRENT_ROOT/" "$staging/"
        differences="$(rsync -a --checksum --delete --dry-run --itemize-changes \
            "$CURRENT_ROOT/" "$staging/")"
        [ -z "$differences" ] || {
            error "数据复制校验存在差异：$differences"
            exit 1
        }
        check_database "$staging/data/handover.db"
        mv -- "$staging" "$TARGET_ROOT"
        success "业务数据已完整复制到数据盘"
    fi
else
    check_database "$TARGET_ROOT/data/handover.db"
    success "当前已经使用目标数据根，无需重复复制"
fi

configure_environment
systemctl start "$SERVICE"
for attempt in {1..30}; do
    if systemctl is-active --quiet "$SERVICE" \
        && curl --fail --silent --max-time 5 \
            "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    [ "$attempt" -lt 30 ] || {
        error "服务健康检查失败，请查看 journalctl -u $SERVICE。"
        exit 1
    }
    sleep 2
done

(cd "$PROJECT/backend" && JX_EXPECTED_DATA_ROOT="$DATA_ROOT" \
    "$VENV/bin/python" - <<'PY'
import os
from pathlib import Path
import sqlite3

from app import config

expected = Path(os.environ["JX_EXPECTED_DATA_ROOT"]).resolve()
if config.USER_DATA_ROOT.resolve() != expected:
    raise SystemExit(f"生效数据根错误：{config.USER_DATA_ROOT.resolve()}")
connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
try:
    check = connection.execute("PRAGMA quick_check").fetchone()
    administrators = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM staff WHERE is_admin=1 ORDER BY name"
        ).fetchall()
    ]
finally:
    connection.close()
if not check or str(check[0]).lower() != "ok":
    raise SystemExit("正式数据库 quick_check 未通过")
if administrators != ["周智源"]:
    raise SystemExit("数据库管理员不是且仅是周智源：" + "、".join(administrators))
PY
)
(cd "$PROJECT" && "$VENV/bin/python" backend/scripts/data_location.py)
(cd "$PROJECT" && "$VENV/bin/python" backend/scripts/manage_admin.py --list)
MIGRATION_COMPLETE=1
echo
success "数据盘迁移完成；旧数据目录仍保留，可在现场验收数日后人工处理。"
echo "数据盘：$DATA_DEVICE -> $DATA_MOUNT"
echo "正式数据根：$DATA_ROOT"
echo "配置副本：$PERSISTED_ENV_FILE"
echo "管理员：周智源（唯一配置）"
