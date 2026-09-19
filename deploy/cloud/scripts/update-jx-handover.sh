#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# ============================================================
# 江西片区智能交接班系统 - 安全一键更新脚本 V0.5.4
# 适配：Alibaba Cloud Linux 4 / Python 3.11 / Node.js 22
# ============================================================

PROJECT="${PROJECT:-/www/wwwroot/jx_handover}"
REPO="${REPO:-https://ghproxy.net/https://github.com/zzyIyzz/jx_handover.git}"
BRANCH="${BRANCH:-release/v0.5.4}"
SERVICE="${SERVICE:-jx-handover}"
PORT="${PORT:-8765}"
DATA_DEVICE="${DATA_DEVICE:-}"
DATA_MOUNT="${DATA_MOUNT:-/data}"
DATA_ROOT="${DATA_ROOT:-/data/jx-handover/data}"
PERSISTED_ENV_FILE="${PERSISTED_ENV_FILE:-/data/jx-handover/config/jx-handover.env}"
ALLOW_SYSTEM_DISK="${ALLOW_SYSTEM_DISK:-1}"

VENV="$PROJECT/.venv"
BACKUP_ROOT="${BACKUP_ROOT:-/www/backup/jx_handover}"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"

PIP_INDEX="${PIP_INDEX:-https://mirrors.cloud.aliyuncs.com/pypi/simple/}"
NPM_REGISTRY_PRIMARY="${NPM_REGISTRY_PRIMARY:-https://registry.npmmirror.com}"
NPM_REGISTRY_FALLBACK="${NPM_REGISTRY_FALLBACK:-https://registry.npmjs.org}"

OLD_COMMIT=""
OLD_BRANCH=""
REMOTE_COMMIT=""
NEW_COMMIT=""
CODE_UPDATED=0
BACKUP_DIR=""
BACKUP_ROOT_REAL=""
LOCAL_STASH_REF=""
HEALTH_FILE=""
UPDATE_SUCCEEDED=0
DEPLOY_STARTED=0
ENV_EXISTED=0
SERVICE_FILE_EXISTED=0
SERVICE_WAS_ACTIVE=0
SERVICE_WAS_ENABLED=0
SERVICE_STOPPED_FOR_BACKUP=0
AI_KEY_INPUT="${QWEN_API_KEY:-}"
AI_KEY_SOURCE=""
AI_KEY_HINT=""
ACCOUNT_SNAPSHOT_FILE=""

# Do not leave the secret exported to child commands.  It is written only to
# the root-readable .env file after the backup has been created.
unset QWEN_API_KEY || true

info() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

success() { echo "[成功] $1"; }
warn() { echo "[警告] $1"; }
error() { echo "[错误] $1" >&2; }

validate_data_disk_mount() {
    local mount_source=""
    local mount_target=""
    local source_device=""
    local expected_device=""
    local root_source=""
    local root_device=""

    mkdir -p -- "$DATA_MOUNT"
    mount_source="$(findmnt -n -o SOURCE --target "$DATA_MOUNT" 2>/dev/null || true)"
    mount_target="$(findmnt -n -o TARGET --target "$DATA_MOUNT" 2>/dev/null || true)"
    mount_source="${mount_source%%[*}"
    source_device="$(readlink -f "$mount_source" 2>/dev/null || true)"
    [ -n "$source_device" ] && [ -b "$source_device" ] || {
        error "无法识别 $DATA_MOUNT 对应的块设备：${mount_source:-未知}"
        return 1
    }
    root_source="$(findmnt -n -o SOURCE --target / 2>/dev/null || true)"
    root_source="${root_source%%[*}"
    root_device="$(readlink -f "$root_source" 2>/dev/null || true)"
    if [ "$source_device" = "$root_device" ]; then
        [ "$ALLOW_SYSTEM_DISK" = "1" ] || {
            error "$DATA_MOUNT 使用系统根分区；当前配置要求独立数据盘。"
            return 1
        }
        warn "当前使用系统盘持久化：$DATA_ROOT；请确保 OSS 异地备份正常。"
    else
        [ "$mount_target" = "$DATA_MOUNT" ] || {
            error "$DATA_MOUNT 位于其他挂载层级，无法确认独立数据盘边界。"
            return 1
        }
    fi
    if [ -n "$DATA_DEVICE" ]; then
        expected_device="$(readlink -f "$DATA_DEVICE" 2>/dev/null || true)"
        [ "$source_device" = "$expected_device" ] || {
            error "$DATA_MOUNT 当前来源为 $source_device，不是指定设备 $DATA_DEVICE。"
            return 1
        }
    else
        DATA_DEVICE="$source_device"
    fi
    [ -w "$DATA_MOUNT" ] || {
        error "数据盘挂载点不可写：$DATA_MOUNT"
        return 1
    }
    success "持久化存储已确认：$DATA_DEVICE -> $DATA_MOUNT"
}

restore_persisted_env_if_needed() {
    if [ ! -f "$PROJECT/.env" ] && [ -f "$PERSISTED_ENV_FILE" ]; then
        cp -a -- "$PERSISTED_ENV_FILE" "$PROJECT/.env"
        chmod 600 "$PROJECT/.env"
        success "已从数据盘恢复正式 .env 配置"
    fi
}

persist_env_to_data_disk() {
    local config_dir=""
    local temporary=""
    config_dir="$(dirname "$PERSISTED_ENV_FILE")"
    mkdir -p -- "$config_dir"
    chmod 700 "$config_dir"
    temporary="$(mktemp "$config_dir/.jx-env.XXXXXX")"
    cp -- "$PROJECT/.env" "$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$PERSISTED_ENV_FILE"
    success "正式 .env 已同步到数据盘：$PERSISTED_ENV_FILE"
}

enforce_production_identity() {
    local session_secret=""
    set_env_value "$PROJECT/.env" JX_HANDOVER_DATA_DIR "$DATA_ROOT"
    set_env_value "$PROJECT/.env" JX_ADMIN_NAMES "周智源"
    set_env_value "$PROJECT/.env" JX_DEFAULT_ADMIN_NAMES "周智源"

    session_secret="$(read_env_value "$PROJECT/.env" JX_SESSION_SECRET 2>/dev/null || true)"
    if [ "${#session_secret}" -lt 32 ] || [[ "$session_secret" == *"请替换"* ]]; then
        session_secret="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
        )"
        set_env_value "$PROJECT/.env" JX_SESSION_SECRET "$session_secret"
        warn "原会话密钥缺失或无效，已生成新的随机密钥；现有浏览器需重新登录。"
    fi
    chmod 600 "$PROJECT/.env"
}

validate_production_env() {
    local configured_root=""
    local admins=""
    local default_admins=""
    configured_root="$(read_env_value "$PROJECT/.env" JX_HANDOVER_DATA_DIR 2>/dev/null || true)"
    admins="$(read_env_value "$PROJECT/.env" JX_ADMIN_NAMES 2>/dev/null || true)"
    default_admins="$(read_env_value "$PROJECT/.env" JX_DEFAULT_ADMIN_NAMES 2>/dev/null || true)"
    [ "$configured_root" = "$DATA_ROOT" ] || {
        error "正式数据根尚未迁移到 $DATA_ROOT。请先运行 prepare-data-disk-v0.5.4.sh。"
        return 1
    }
    [ "$admins" = "周智源" ] && [ "$default_admins" = "周智源" ] || {
        error "管理员配置必须且只能为周智源；请先运行数据盘准备脚本。"
        return 1
    }
}

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
    local temp=""
    temp="$(mktemp "${file}.tmp.XXXXXX")"
    JX_ENV_REPLACEMENT="$value" awk -v wanted="$key" '
        BEGIN { done=0; replacement=ENVIRON["JX_ENV_REPLACEMENT"] }
        $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
            if (!done) print wanted "=" replacement
            done=1
            next
        }
        { print }
        END { if (!done) print wanted "=" replacement }
    ' "$file" > "$temp"
    chmod --reference="$file" "$temp" 2>/dev/null || chmod 600 "$temp"
    chown --reference="$file" "$temp" 2>/dev/null || true
    mv -f -- "$temp" "$file"
}

is_real_ai_key() {
    local value="${1:-}"
    [ -n "$value" ] \
        && [[ "$value" != *"你的"* ]] \
        && [[ "$value" != *"your"* ]] \
        && [[ "$value" != *"YOUR"* ]] \
        && [[ "$value" != *"<"* ]] \
        && [[ "$value" != *">"* ]]
}

find_saved_ai_key() {
    local candidate=""
    local value=""
    local -a candidates=()

    if is_real_ai_key "$AI_KEY_INPUT"; then
        AI_KEY_SOURCE="执行脚本时提供的 QWEN_API_KEY"
        return 0
    fi

    value="$(read_env_value "$PROJECT/.env" QWEN_API_KEY 2>/dev/null || true)"
    if is_real_ai_key "$value"; then
        AI_KEY_INPUT="$value"
        AI_KEY_SOURCE="现有 $PROJECT/.env"
        return 0
    fi

    mapfile -t candidates < <(
        find "$BACKUP_ROOT_REAL" -mindepth 2 -maxdepth 2 -type f \
            \( -name '.env' -o -name '.env.example.server-copy' \) \
            -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-
    )
    for candidate in "${candidates[@]}"; do
        value="$(read_env_value "$candidate" QWEN_API_KEY 2>/dev/null || true)"
        if is_real_ai_key "$value"; then
            AI_KEY_INPUT="$value"
            AI_KEY_SOURCE="历史备份 $(dirname "$candidate")"
            return 0
        fi
    done
    return 1
}

configure_ai() {
    local env_file="$PROJECT/.env"
    local old_mode=""

    old_mode="$(read_env_value "$env_file" AI_MODE 2>/dev/null || true)"
    set_env_value "$env_file" AI_MODE auto
    set_env_value "$env_file" QWEN_BASE_URL \
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    set_env_value "$env_file" QWEN_MODEL "qwen3.8-flash"
    set_env_value "$env_file" AI_STRUCTURED_MODE "json_schema"
    set_env_value "$env_file" AI_TIMEOUT_SECONDS "60"

    if find_saved_ai_key; then
        set_env_value "$env_file" QWEN_API_KEY "$AI_KEY_INPUT"
        chmod 600 "$env_file"
        AI_KEY_HINT="****${AI_KEY_INPUT: -4}"
        success "Qwen AI 已配置为 auto；密钥已从${AI_KEY_SOURCE}安全迁移（${AI_KEY_HINT}）"
    else
        set_env_value "$env_file" QWEN_API_KEY ""
        chmod 600 "$env_file"
        warn "已将 AI_MODE 从 ${old_mode:-未配置} 调整为 auto，但没有找到真实 QWEN_API_KEY。"
        warn "本次升级会继续完成；随后请运行同目录的 restore-ai.sh 安全填写 Key。"
    fi
}

snapshot_account_credentials() {
    ACCOUNT_SNAPSHOT_FILE="$BACKUP_DIR/account-credentials-before.json"
    local result=""
    result="$(cd "$PROJECT/backend" && \
        JX_ACCOUNT_SNAPSHOT_FILE="$ACCOUNT_SNAPSHOT_FILE" \
        "$VENV/bin/python" - <<'PY'
import json
import os
from pathlib import Path
import sqlite3

from app import config

target = Path(os.environ["JX_ACCOUNT_SNAPSHOT_FILE"])
payload = {
    "database_path": str(config.DATABASE_PATH.resolve()),
    "supported": False,
    "accounts": [],
}
if config.DATABASE_PATH.is_file():
    connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='staff'"
        ).fetchone()
        if table:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(staff)")
            }
            required = {
                "id", "name", "password_hash", "must_change_password",
                "password_updated_at",
            }
            if required.issubset(columns):
                payload["supported"] = True
                rows = connection.execute(
                    "SELECT id, name, password_hash, must_change_password, "
                    "password_updated_at FROM staff "
                    "WHERE COALESCE(password_hash, '') <> '' ORDER BY id"
                ).fetchall()
                payload["accounts"] = [
                    {
                        "id": str(row[0]), "name": str(row[1] or ""),
                        "password_hash": str(row[2] or ""),
                        "must_change_password": int(row[3] or 0),
                        "password_updated_at": row[4],
                    }
                    for row in rows
                ]
    finally:
        connection.close()
with target.open("w", encoding="utf-8") as stream:
    json.dump(payload, stream, ensure_ascii=False, indent=2)
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(target, 0o600)
print(json.dumps({
    "supported": payload["supported"],
    "accounts": len(payload["accounts"]),
}, ensure_ascii=False))
PY
    )" || {
        error "无法生成账号密码连续性快照，拒绝继续更新。"
        return 1
    }
    chmod 600 "$ACCOUNT_SNAPSHOT_FILE"
    echo "账号密码快照：$result"
    success "已有账号密码状态已保存（仅含密码哈希，不含明文密码）"
}

verify_account_credentials() {
    [ -n "$ACCOUNT_SNAPSHOT_FILE" ] && [ -f "$ACCOUNT_SNAPSHOT_FILE" ] || return 0
    local result=""
    result="$(cd "$PROJECT" && "$VENV/bin/python" \
        backend/scripts/account_continuity.py verify \
        --snapshot "$ACCOUNT_SNAPSHOT_FILE")" || {
        error "账号密码连续性检查失败，备份位置：$BACKUP_DIR"
        return 1
    }
    echo "账号密码核对：$result"
    if grep -Eq '"supported"[[:space:]]*:[[:space:]]*false' <<<"$result"; then
        warn "旧数据库此前没有密码字段，已有人员已按首次账号迁移规则初始化。"
    elif grep -Eq '"restored"[[:space:]]*:[[:space:]]*0' <<<"$result"; then
        success "升级前已有账号及密码保持不变"
    else
        warn "检测到密码字段异常变化，已从升级前快照恢复；请检查服务日志。"
    fi
}

run_database_preflight() {
    (cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config
from app.bootstrap import initialize_application_data
from app.services.backup import apply_pending_restore
from app.services.data_root import adopt_legacy_data_root

config.validate_runtime_configuration()
if apply_pending_restore():
    raise RuntimeError("升级期间不应执行数据恢复任务。")
adoption = adopt_legacy_data_root()
if adoption.get("state") in {"ambiguous", "failed"}:
    raise RuntimeError(adoption.get("message") or "数据目录接管失败。")
result = initialize_application_data()
print(
    "数据库预检完成："
    f"新账号 {result['created_staff']}，首次初始化密码 {result['initialized_accounts']}"
)
PY
    )
}

verify_production_state() {
    (cd "$PROJECT/backend" && JX_EXPECTED_DATA_ROOT="$DATA_ROOT" \
        "$VENV/bin/python" - <<'PY'
import os
from pathlib import Path
import sqlite3

from app import config

expected = Path(os.environ["JX_EXPECTED_DATA_ROOT"]).resolve()
if config.USER_DATA_ROOT.resolve() != expected:
    raise RuntimeError(
        f"生效数据根错误：{config.USER_DATA_ROOT.resolve()}，期望 {expected}"
    )
if config.ADMIN_NAMES != {"周智源"} or config.DEFAULT_ADMIN_NAMES != {"周智源"}:
    raise RuntimeError("管理员环境配置没有锁定为周智源一人。")
connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
try:
    check = connection.execute("PRAGMA quick_check").fetchone()
    if not check or str(check[0]).lower() != "ok":
        raise RuntimeError("正式数据库完整性检查失败。")
    administrators = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM staff WHERE is_admin=1 ORDER BY name"
        ).fetchall()
    ]
finally:
    connection.close()
if administrators != ["周智源"]:
    raise RuntimeError(
        "数据库管理员不是且仅是周智源：" + "、".join(administrators)
    )
print(f"正式数据根：{expected}")
print("数据库管理员：周智源（唯一）")
print("数据库完整性：ok")
PY
    )
}

sync_root_tools() {
    local scripts_dir="$PROJECT/deploy/cloud/scripts"
    local source=""
    local target=""
    local -a mappings=(
        "update_jx_handover.sh:/root/update_jx_handover.sh"
        "prepare-data-disk-v0.5.4.sh:/root/prepare-data-disk-v0.5.4.sh"
        "restore-ai.sh:/root/restore-ai.sh"
        "one-click-v0.5.4.sh:/root/one-click-v0.5.4.sh"
    )
    for mapping in "${mappings[@]}"; do
        source="$scripts_dir/${mapping%%:*}"
        target="${mapping#*:}"
        [ -f "$source" ] || {
            error "新版仓库缺少 root 工具：$source"
            return 1
        }
        install -o root -g root -m 700 "$source" "$target"
    done
    success "root 升级、数据盘和 AI 工具已同步到本次最新版本"
}

cleanup_temp() {
    if [ -n "$HEALTH_FILE" ] && [ -f "$HEALTH_FILE" ]; then
        rm -f -- "$HEALTH_FILE"
    fi
}

show_npm_log() {
    local logfile=""
    logfile="$(find /root/.npm/_logs -maxdepth 1 -type f -name '*-debug-0.log' -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | head -n 1 | cut -d' ' -f2- || true)"
    if [ -n "$logfile" ] && [ -f "$logfile" ]; then
        echo
        echo "========== 最近 npm 错误日志 =========="
        tail -n 120 "$logfile" || true
        echo "========================================="
    fi
}

install_python_requirements() {
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    export PIP_INDEX_URL="$PIP_INDEX"
    export PIP_DISABLE_PIP_VERSION_CHECK=1
    export PIP_NO_CACHE_DIR=1
    export PIP_DEFAULT_TIMEOUT=120

    python -m pip --version
    python -m pip install --no-cache-dir --retries 5 --timeout 120 \
        -r "$PROJECT/backend/requirements.txt"
}

install_frontend() {
    cd "$PROJECT/frontend"

    export npm_config_fetch_retries=5
    export npm_config_fetch_retry_mintimeout=20000
    export npm_config_fetch_retry_maxtimeout=120000
    export npm_config_fetch_timeout=300000

    rm -rf -- "$PROJECT/frontend/node_modules"

    if [ -f "$PROJECT/frontend/package-lock.json" ]; then
        echo "检测到 package-lock.json，使用 npm ci（严格按锁文件安装）"
        if ! npm ci --registry="$NPM_REGISTRY_PRIMARY" --no-audit --no-fund; then
            warn "npmmirror 安装失败，使用 npm 官方 registry 再重试一次"
            rm -rf -- "$PROJECT/frontend/node_modules"
            if ! npm ci --registry="$NPM_REGISTRY_FALLBACK" --no-audit --no-fund; then
                error "npm ci 失败；不会自动执行 npm install，也不会修改 package-lock.json。"
                show_npm_log
                return 1
            fi
        fi
    else
        warn "仓库没有 package-lock.json，只能使用 npm install"
        if ! npm install --registry="$NPM_REGISTRY_PRIMARY" --no-audit --no-fund; then
            warn "npmmirror 安装失败，使用 npm 官方 registry 再重试一次"
            npm install --registry="$NPM_REGISTRY_FALLBACK" --no-audit --no-fund
        fi
    fi

    npm run build
    if [ ! -f "$PROJECT/frontend/dist/index.html" ]; then
        error "前端构建结束，但未发现 frontend/dist/index.html"
        return 1
    fi
}

backup_git_state() {
    git status --short --branch > "$BACKUP_DIR/git-status.txt"
    git diff --binary > "$BACKUP_DIR/git-working-tree.patch"
    git diff --cached --binary > "$BACKUP_DIR/git-index.patch"
}

ensure_local_runtime_ignores() {
    local exclude_file="$PROJECT/.git/info/exclude"
    local pattern=""
    local -a patterns=(
        "/runtime/"
        "/runtime-server/"
        "/backend/runtime/"
        "/backend/runtime-server/"
    )
    touch "$exclude_file"
    for pattern in "${patterns[@]}"; do
        if ! grep -Fqx "$pattern" "$exclude_file"; then
            printf '%s\n' "$pattern" >> "$exclude_file"
        fi
    done
}

handle_local_changes() {
    local changed_file=""
    local -a changed_files=()

    mapfile -t changed_files < <(
        {
            git diff --name-only
            git diff --cached --name-only
            git ls-files --others --exclude-standard
        } | sed '/^$/d' | sort -u
    )

    if [ "${#changed_files[@]}" -eq 0 ]; then
        success "Git 工作区干净"
        return 0
    fi

    warn "发现服务器项目目录存在未提交修改："
    git status --short
    backup_git_state

    if [ "${#changed_files[@]}" -ne 1 ] || [ "${changed_files[0]}" != ".env.example" ]; then
        echo
        error "除 .env.example 外还存在其他本地修改，本次更新安全终止。"
        error "修改清单和补丁已保存到：$BACKUP_DIR"
        error "脚本没有覆盖或删除这些文件，请人工确认后重新执行。"
        return 1
    fi

    changed_file="${changed_files[0]}"
    cp -a -- "$PROJECT/$changed_file" "$BACKUP_DIR/.env.example.server-copy"

    warn "检测到的唯一修改是 .env.example，将先完整归档再继续更新。"
    git stash push --message "jx-handover-env-example-$(date +%Y%m%d_%H%M%S)" -- "$changed_file"
    LOCAL_STASH_REF="$(git rev-parse 'stash@{0}')"
    printf '%s\n' "$LOCAL_STASH_REF" > "$BACKUP_DIR/git-stash-commit.txt"

    if [ -n "$(git status --porcelain)" ]; then
        error "归档 .env.example 后工作区仍不干净，本次更新安全终止。"
        git status --short
        return 1
    fi

    success ".env.example 已保存到 Git stash 和外部备份"
    echo "stash Commit：$LOCAL_STASH_REF"
    echo "外部备份：$BACKUP_DIR/.env.example.server-copy"
}

rollback() {
    local exit_code="$1"
    trap - EXIT

    echo
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "更新未完成（退出码：$exit_code）"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"

    if [ "$DEPLOY_STARTED" = "1" ] && [ -n "$OLD_COMMIT" ] && [ -d "$PROJECT/.git" ]; then
        warn "尝试将代码回退到更新前 Commit：$OLD_COMMIT"
        cd "$PROJECT" || true

        if [ -n "$OLD_BRANCH" ] && git show-ref --verify --quiet "refs/heads/$OLD_BRANCH"; then
            if git checkout --force "$OLD_BRANCH" \
                && [ "$(git symbolic-ref --quiet --short HEAD || true)" = "$OLD_BRANCH" ]; then
                git reset --hard "$OLD_COMMIT" || true
            else
                error "无法切回更新前分支 $OLD_BRANCH；为避免重置错误分支，已停止 Git 自动回滚。"
            fi
        else
            git checkout --force --detach "$OLD_COMMIT" || \
                error "无法恢复到更新前的分离 HEAD：$OLD_COMMIT"
        fi

        if [ "$ENV_EXISTED" = "1" ] && [ -f "$BACKUP_DIR/.env" ]; then
            cp -a -- "$BACKUP_DIR/.env" "$PROJECT/.env" || true
        elif [ "$ENV_EXISTED" = "0" ]; then
            rm -f -- "$PROJECT/.env" || true
        fi

        if [ "$SERVICE_FILE_EXISTED" = "1" ] && [ -f "$BACKUP_DIR/${SERVICE}.service" ]; then
            cp -a -- "$BACKUP_DIR/${SERVICE}.service" "$SERVICE_FILE" || true
        elif [ "$SERVICE_FILE_EXISTED" = "0" ]; then
            rm -f -- "$SERVICE_FILE" || true
        fi
        systemctl daemon-reload || true

        if [ -d "$VENV" ] && [ -f "$PROJECT/backend/requirements.txt" ]; then
            install_python_requirements || true
        fi
        if [ -d "$PROJECT/frontend" ]; then
            install_frontend || true
        fi

        if [ "$SERVICE_WAS_ENABLED" = "1" ]; then
            systemctl enable "$SERVICE" >/dev/null 2>&1 || true
        else
            systemctl disable "$SERVICE" >/dev/null 2>&1 || true
        fi
        if [ "$SERVICE_WAS_ACTIVE" = "1" ]; then
            systemctl restart "$SERVICE" 2>/dev/null || \
                error "旧服务未能重新启动，请立即检查 journalctl。"
        else
            systemctl stop "$SERVICE" 2>/dev/null || true
        fi
        warn "已尝试恢复更新前代码、配置、依赖和服务状态。"
    fi

    if [ "$DEPLOY_STARTED" = "0" ] && [ "$SERVICE_STOPPED_FOR_BACKUP" = "1" ]; then
        systemctl start "$SERVICE" || error "备份失败后旧服务未能重新启动，请立即检查 journalctl。"
    fi

    if [ -n "$LOCAL_STASH_REF" ]; then
        warn "原 .env.example 修改仍保存在 Git stash：$LOCAL_STASH_REF"
        echo "需要查看时：cd '$PROJECT' && git stash list"
    fi
    if [ -n "$BACKUP_DIR" ]; then
        echo "本次备份目录：$BACKUP_DIR"
    fi

    echo "正式数据不会被自动覆盖恢复，避免误覆盖运行数据。"
    echo "查看服务日志：journalctl -u $SERVICE -n 100 --no-pager"
    cleanup_temp
    exit "$exit_code"
}

on_exit() {
    local exit_code=$?
    if [ "$exit_code" -ne 0 ] && [ "$UPDATE_SUCCEEDED" != "1" ]; then
        rollback "$exit_code"
    fi
    cleanup_temp
}

trap on_exit EXIT

main() {
    clear 2>/dev/null || true

    echo "============================================================"
    echo "       江西片区智能交接班系统 - 安全一键更新"
    echo "============================================================"
    echo
    echo "GitHub：$REPO"
    echo "分支：$BRANCH"
    echo "目录：$PROJECT"
    echo "端口：$PORT"
    echo

    # 1. 环境检查
    info "1/10 检查服务器环境"

    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        error "请使用 root 权限执行：sudo bash $0"
        return 1
    fi

    local required_commands=(git python3 node npm curl systemctl tar find sort sed readlink findmnt)
    local cmd=""
    for cmd in "${required_commands[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            error "缺少命令：$cmd"
            return 1
        fi
    done

    mkdir -p "$BACKUP_ROOT"
    BACKUP_ROOT_REAL="$(readlink -f "$BACKUP_ROOT")"
    if [ -z "$BACKUP_ROOT_REAL" ] || [ "$BACKUP_ROOT_REAL" = "/" ] \
        || [[ "$BACKUP_ROOT_REAL" != /* ]]; then
        error "BACKUP_ROOT 必须是有效的绝对目录，且不能是根目录：$BACKUP_ROOT"
        return 1
    fi
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$BACKUP_ROOT/.update.lock"
        if ! flock -n 9; then
            error "已有另一个更新进程正在运行，请勿重复执行。"
            return 1
        fi
    else
        warn "系统没有 flock，无法启用并发更新锁。"
    fi

    success "服务器基本环境正常"
    echo "Git：$(git --version)"
    echo "Python：$(python3 --version)"
    echo "Node：$(node --version)"
    echo "NPM：$(npm --version)"

    # 2. 项目检查 / 首次 Clone
    info "2/10 检查项目"

    if [ ! -d "$PROJECT/.git" ]; then
        if [ -e "$PROJECT" ] && [ -n "$(find "$PROJECT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
            error "项目目录已存在且非空，但不是 Git 仓库：$PROJECT"
            return 1
        fi
        warn "未发现现有项目，开始首次拉取"
        mkdir -p "$(dirname "$PROJECT")"
        git clone --branch "$BRANCH" --single-branch "$REPO" "$PROJECT"
        success "GitHub 项目下载完成"
    else
        success "已发现现有项目"
    fi

    cd "$PROJECT"

    # Runtime directories are business data, never source-code changes. Older
    # checkouts did not ignore the directory-level adoption report, so keep a
    # local exclude before inspecting the working tree.
    ensure_local_runtime_ignores

    validate_data_disk_mount
    restore_persisted_env_if_needed
    if [ ! -f "$PROJECT/.env" ]; then
        error "缺少正式 .env；请先运行 prepare-data-disk-v0.5.4.sh 完成数据盘初始化。"
        return 1
    fi
    validate_production_env

    # 3. Git 检查
    # Check before deployment so even rollback cannot trigger a pending restore.
    if [ -x "$VENV/bin/python" ]; then
        (cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from pathlib import Path

from app import config
from app.services.data_root import adoption_plan

markers = [config.SNAPSHOT_DIR / "restore" / "pending.json"]
plan = adoption_plan()
if plan.get("state") == "adopt" and plan.get("source"):
    source = Path(str(plan["source"]))
    markers.append(source / "snapshots" / "restore" / "pending.json")
blocked = [
    marker
    for marker in markers
    if marker.exists() or marker.with_name("applying.json").exists()
]
if blocked:
    print("[错误] 存在待执行或未完成的数据恢复；请先按恢复验收文档核实，不能直接更新。")
    for marker in blocked:
        print(f"[错误] 恢复标记目录：{marker.parent}")
    raise SystemExit(1)
PY
        )
    elif [ -f "$PROJECT/.env" ]; then
        error "现有项目缺少可用的 Python 虚拟环境，无法安全检查待恢复状态。"
        return 1
    fi

    info "3/10 检查 Git 和目标发布分支"

    git remote set-url origin "$REPO"
    # 使用完整 refspec，确保 origin/$BRANCH 一定更新，而不只是 FETCH_HEAD。
    git fetch --prune origin \
        "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"

    if ! git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
        error "远程分支不存在：origin/$BRANCH"
        return 1
    fi

    OLD_COMMIT="$(git rev-parse HEAD)"
    OLD_BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
    REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH")"

    echo "当前分支：${OLD_BRANCH:-（分离 HEAD）}"
    echo "当前 Commit：$OLD_COMMIT"
    echo "远程分支：origin/$BRANCH"
    echo "远程 Commit：$REMOTE_COMMIT"

    # 已存在的目标分支如果含远程没有的提交，不自动抹掉。
    if git show-ref --verify --quiet "refs/heads/$BRANCH" \
        && ! git merge-base --is-ancestor "$BRANCH" "origin/$BRANCH"; then
        error "本地 $BRANCH 含远程分支没有的提交，无法安全快进。"
        error "请人工确认这些提交后再更新；脚本没有重置本地提交。"
        return 1
    fi

    local timestamp=""
    timestamp="$(date +%Y%m%d_%H%M%S)_${BASHPID}"
    BACKUP_DIR="$BACKUP_ROOT_REAL/$timestamp"
    if ! mkdir "$BACKUP_DIR"; then
        error "无法创建唯一备份目录：$BACKUP_DIR"
        return 1
    fi

    handle_local_changes

    # 4. 备份正式数据和配置
    info "4/10 备份正式数据、配置和服务文件"

    local backed_up=0
    if [ -f "$PROJECT/.env" ]; then
        ENV_EXISTED=1
        cp -a -- "$PROJECT/.env" "$BACKUP_DIR/.env"
        success ".env 已备份"
    fi

    if [ -f "$SERVICE_FILE" ]; then
        SERVICE_FILE_EXISTED=1
        cp -a -- "$SERVICE_FILE" "$BACKUP_DIR/${SERVICE}.service"
        success "systemd 服务文件已备份"
    fi

    if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        SERVICE_WAS_ACTIVE=1
    fi
    if systemctl is-enabled --quiet "$SERVICE" 2>/dev/null; then
        SERVICE_WAS_ENABLED=1
    fi

    # Stop the sole application writer before archiving SQLite DB/WAL and files.
    # Failures before DEPLOY_STARTED restart the original service without reset.
    if [ "$SERVICE_WAS_ACTIVE" = "1" ]; then
        SERVICE_STOPPED_FOR_BACKUP=1
        systemctl stop "$SERVICE"
    fi
    if systemctl is-active --quiet "$SERVICE"; then
        error "服务仍在运行，拒绝制作可能不一致的数据备份。"
        return 1
    fi

    if [ -x "$VENV/bin/python" ]; then
        snapshot_account_credentials
    else
        warn "首次部署尚无虚拟环境和既有账号，跳过账号密码快照。"
    fi

    local data_name=""
    for data_name in runtime runtime-server; do
        if [ -d "$PROJECT/$data_name" ]; then
            tar -czf "$BACKUP_DIR/$data_name.tar.gz" -C "$PROJECT" "$data_name"
            tar -tzf "$BACKUP_DIR/$data_name.tar.gz" >/dev/null
            success "$data_name 已在停服状态下备份并校验"
            backed_up=1
        fi
    done

    local custom_data_dir=""
    if [ -x "$VENV/bin/python" ]; then
        custom_data_dir="$(cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config
print(config.USER_DATA_ROOT.resolve())
PY
        )"
    elif [ -f "$PROJECT/.env" ]; then
        custom_data_dir="$(grep -E '^JX_HANDOVER_DATA_DIR=' "$PROJECT/.env" | tail -n 1 \
            | cut -d '=' -f2- | tr -d '\r' \
            | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true)"
    fi

    if [ -n "$custom_data_dir" ] && [ -d "$custom_data_dir" ]; then
        local custom_real=""
        local default_real=""
        custom_real="$(readlink -f "$custom_data_dir")"
        default_real="$(readlink -f "$PROJECT/runtime-server" 2>/dev/null || true)"
        if [ -z "$custom_real" ] || [ "$custom_real" = "/" ] \
            || [ "$custom_real" = "$BACKUP_ROOT_REAL" ] \
            || [[ "$PROJECT/" == "$custom_real/"* ]] \
            || [[ "$BACKUP_ROOT_REAL/" == "$custom_real/"* ]]; then
            error "JX_HANDOVER_DATA_DIR 指向过宽或危险的目录：$custom_real"
            return 1
        fi
        if [ "$custom_real" != "$default_real" ] \
            && [ "$custom_real" != "$(readlink -f "$PROJECT/runtime" 2>/dev/null || true)" ]; then
            tar -czf "$BACKUP_DIR/custom-data.tar.gz" -C "$custom_real" .
            tar -tzf "$BACKUP_DIR/custom-data.tar.gz" >/dev/null
            success "自定义数据目录已备份并校验：$custom_real"
            backed_up=1
        fi
    fi

    if [ "$backed_up" = "0" ]; then
        warn "当前未发现运行数据目录，首次部署时属于正常现象"
    fi
    echo "本次备份位置：$BACKUP_DIR"

    # 从这里开始，任何失败都需要恢复代码、配置和服务状态。
    DEPLOY_STARTED=1

    # 5. 快进同步发布分支
    info "5/10 同步 GitHub 发布分支"

    echo "更新前：$OLD_COMMIT"
    echo "发布版：$REMOTE_COMMIT"

    if [ "$OLD_BRANCH" != "$BRANCH" ] || [ "$OLD_COMMIT" != "$REMOTE_COMMIT" ]; then
        CODE_UPDATED=1
        if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
            git checkout "$BRANCH"
        else
            git checkout -b "$BRANCH" --track "origin/$BRANCH"
        fi
        git merge --ff-only "origin/$BRANCH"
    fi

    NEW_COMMIT="$(git rev-parse HEAD)"
    if [ "$NEW_COMMIT" != "$REMOTE_COMMIT" ]; then
        error "同步后 HEAD 与 origin/$BRANCH 不一致。"
        return 1
    fi
    success "服务器代码已同步到 origin/$BRANCH"

    git log -1 --pretty=format:"当前代码：%h%n提交说明：%s%n提交时间：%cd" \
        --date=format:"%Y-%m-%d %H:%M:%S"
    echo

    # 6. .env
    info "6/10 检查服务端配置"

    if [ ! -f "$PROJECT/.env" ]; then
        if [ ! -f "$PROJECT/.env.example" ]; then
            error "仓库中不存在 .env.example，无法自动创建 .env"
            return 1
        fi
        cp "$PROJECT/.env.example" "$PROJECT/.env"
        warn "未发现 .env，已从新版 .env.example 创建；请核对业务配置。"
    fi

    set_env_value "$PROJECT/.env" JX_HANDOVER_MODE server
    enforce_production_identity
    configure_ai
    persist_env_to_data_disk
    success ".env 已保留，数据根和管理员已锁定，AI_MODE=auto"

    # 7. Python
    info "7/10 更新 Python 后端"
    cd "$PROJECT"
    if [ ! -d "$VENV" ]; then
        echo "首次创建 Python 虚拟环境..."
        python3 -m venv "$VENV"
    fi
    install_python_requirements
    success "Python 后端依赖更新完成"

    # 8. Vue
    info "8/10 编译 Vue 前端"
    install_frontend
    success "Vue 前端依赖安装及构建完成"

    # 9. systemd
    info "9/10 配置并重启后台服务"

    run_database_preflight
    verify_account_credentials
    verify_production_state
    success "数据库迁移与账号密码连续性检查通过"

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=JX Handover System
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT/backend
EnvironmentFile=-$PROJECT/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$PROJECT/backend
ExecStart=$VENV/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null
    systemctl restart "$SERVICE"
    sleep 3

    if ! systemctl is-active --quiet "$SERVICE"; then
        error "交接班服务启动失败"
        journalctl -u "$SERVICE" -n 100 --no-pager
        return 1
    fi
    success "systemd 服务运行正常"

    # 10. 健康检查
    info "10/10 系统健康检查"

    local health_url="http://127.0.0.1:${PORT}/api/health"
    local health_ok=0
    local i=0
    HEALTH_FILE="$(mktemp /tmp/jx_handover_health.XXXXXX.json)"

    for i in {1..15}; do
        if curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
            "$health_url" > "$HEALTH_FILE" 2>/dev/null; then
            health_ok=1
            break
        fi
        echo "等待服务启动 ($i/15)..."
        sleep 2
    done

    if [ "$health_ok" != "1" ]; then
        error "API 健康检查失败"
        journalctl -u "$SERVICE" -n 100 --no-pager
        return 1
    fi

    success "API 健康检查通过"
    cat "$HEALTH_FILE"
    echo

    info "AI 配置检查"
    local ai_check=""
    ai_check="$(cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config

hint = f"****{config.QWEN_API_KEY[-4:]}" if config.QWEN_API_KEY else "未填写"
print(f"requested={config.AI_MODE_REQUESTED}")
print(f"effective={config.AI_MODE}")
print(f"model={config.QWEN_MODEL}")
print(f"key={hint}")
if config.AI_MODE_REQUESTED != "auto":
    raise SystemExit(2)
if config.QWEN_API_KEY and config.AI_MODE != "qwen":
    raise SystemExit(3)
PY
)"
    printf '%s\n' "$ai_check"
    if grep -q '^effective=qwen$' <<<"$ai_check"; then
        echo "正在调用 Qwen 做真实连接测试……"
        if (cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app.services.ai.adapter import test_qwen_connection
result = test_qwen_connection()
print(result.get("message", ""))
if not result.get("ok") or result.get("mode") != "qwen":
    raise SystemExit(1)
PY
        ); then
            success "真实 Qwen AI 已通过连接测试。"
        else
            warn "程序已升级，但真实 Qwen 连接未通过；当前仍可能回退本地规则。"
            warn "请检查 Key 权限、余额和外网，然后运行 restore-ai.sh；不会因 AI 外网故障回滚 Excel 修复。"
        fi
    else
        warn "AI 代码已恢复为自动模式，但尚无 Key，当前仍会使用本地规则。"
        warn "执行：sudo env PROJECT='$PROJECT' SERVICE='$SERVICE' bash restore-ai.sh"
    fi

    info "同步 /root 运维工具"
    sync_root_tools

    info "清理历史备份"
    local -a old_backups=()
    local old_backup=""
    local old_backup_real=""
    local old_backup_name=""
    local valid_index=0
    mapfile -t old_backups < <(
        find "$BACKUP_ROOT_REAL" -mindepth 1 -maxdepth 1 -type d \
            -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-
    )
    for old_backup in "${old_backups[@]}"; do
        old_backup_name="$(basename "$old_backup")"
        if [[ ! "$old_backup_name" =~ ^20[0-9]{6}_[0-9]{6}_[0-9]+$ ]]; then
            continue
        fi
        valid_index=$((valid_index + 1))
        if [ "$valid_index" -le 10 ] || [ "$old_backup" = "$BACKUP_DIR" ]; then
            continue
        fi
        old_backup_real="$(readlink -f "$old_backup" 2>/dev/null || true)"
        if [ -n "$old_backup_real" ] \
            && [ "$(dirname "$old_backup_real")" = "$BACKUP_ROOT_REAL" ] \
            && [ "$old_backup_real" != "$BACKUP_DIR" ]; then
            if ! rm -rf -- "$old_backup_real"; then
                warn "旧备份清理失败（不影响本次更新）：$old_backup_real"
            fi
        else
            warn "跳过异常备份路径：$old_backup"
        fi
    done
    success "仅保留最近 10 次更新备份"

    UPDATE_SUCCEEDED=1
    cleanup_temp

    echo
    echo "############################################################"
    echo "#                    更新成功                              #"
    echo "############################################################"
    echo "项目目录：$PROJECT"
    echo "GitHub 分支：$BRANCH"
    echo "更新前：$OLD_COMMIT"
    echo "更新后：$NEW_COMMIT"
    echo "备份目录：$BACKUP_DIR"
    echo "服务名称：$SERVICE"
    echo "监听端口：$PORT"

    if [ -n "$LOCAL_STASH_REF" ]; then
        echo
        echo "原 .env.example 修改已归档，不会自动恢复："
        echo "  stash Commit：$LOCAL_STASH_REF"
        echo "  外部副本：$BACKUP_DIR/.env.example.server-copy"
        echo "  请人工将仍需要的配置项迁移到正式 .env。"
    fi

    echo
    git log -1 --pretty=format:"当前版本：%h%n提交说明：%s%n提交时间：%cd" \
        --date=format:"%Y-%m-%d %H:%M:%S"
    echo
    echo
    echo "健康检查：$health_url"
    echo "浏览器访问：http://你的服务器IP:$PORT"
    echo
}

main "$@"
