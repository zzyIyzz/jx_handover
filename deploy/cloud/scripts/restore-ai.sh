#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 江西片区智能交接班系统 - Qwen AI 安全恢复工具
# 推荐：sudo bash restore-ai.sh
# 非交互：sudo env QWEN_API_KEY='真实Key' bash restore-ai.sh

PROJECT="${PROJECT:-/www/wwwroot/jx_handover}"
SERVICE="${SERVICE:-jx-handover}"
PORT="${PORT:-8765}"
BACKUP_ROOT="${BACKUP_ROOT:-/www/backup/jx_handover}"
VENV="$PROJECT/.venv"
ENV_FILE="$PROJECT/.env"
INPUT_KEY="${QWEN_API_KEY:-}"
KEY_SOURCE=""
unset QWEN_API_KEY || true

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

is_real_key() {
    local value="${1:-}"
    [ -n "$value" ] \
        && [[ "$value" != *"你的"* ]] \
        && [[ "$value" != *"your"* ]] \
        && [[ "$value" != *"YOUR"* ]] \
        && [[ "$value" != *"<"* ]] \
        && [[ "$value" != *">"* ]]
}

find_existing_key() {
    local value=""
    local candidate=""
    local backup_root_real=""
    local -a candidates=()

    if is_real_key "$INPUT_KEY"; then
        KEY_SOURCE="执行命令时提供的 QWEN_API_KEY"
        return 0
    fi

    value="$(read_env_value "$ENV_FILE" QWEN_API_KEY 2>/dev/null || true)"
    if is_real_key "$value"; then
        INPUT_KEY="$value"
        KEY_SOURCE="当前 .env"
        return 0
    fi

    backup_root_real="$(readlink -f "$BACKUP_ROOT" 2>/dev/null || true)"
    if [ -n "$backup_root_real" ] && [ "$backup_root_real" != "/" ]; then
        mapfile -t candidates < <(
            find "$backup_root_real" -mindepth 2 -maxdepth 2 -type f \
                \( -name '.env' -o -name '.env.example.server-copy' \) \
                -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-
        )
        for candidate in "${candidates[@]}"; do
            value="$(read_env_value "$candidate" QWEN_API_KEY 2>/dev/null || true)"
            if is_real_key "$value"; then
                INPUT_KEY="$value"
                KEY_SOURCE="历史备份 $(dirname "$candidate")"
                return 0
            fi
        done
    fi
    return 1
}

echo "============================================================"
echo " 江西片区智能交接班系统 - 恢复 Qwen AI"
echo "============================================================"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    error "请使用 root 权限执行：sudo bash $0"
    exit 1
fi
for command_name in awk find sort readlink curl systemctl; do
    command -v "$command_name" >/dev/null 2>&1 || {
        error "缺少命令：$command_name"
        exit 1
    }
done
if [ ! -d "$PROJECT" ] || [ ! -f "$PROJECT/backend/app/config.py" ]; then
    error "项目目录无效：$PROJECT"
    exit 1
fi
if [ ! -x "$VENV/bin/python" ]; then
    error "Python 虚拟环境不存在：$VENV"
    exit 1
fi
# Refuse to trigger a pending database restore as a side effect of AI recovery.
if ! (cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config
markers = [config.SNAPSHOT_DIR / "restore" / "pending.json"]
markers += [config.SOURCE_BASE / name / "snapshots" / "restore" / "pending.json"
            for name in ("runtime", "runtime-server")]
if any(marker.exists() or marker.with_name("applying.json").exists() for marker in markers):
    print("[错误] 存在待执行或未完成的数据恢复；请先按恢复验收文档核实，不能直接重启服务。")
    raise SystemExit(1)
PY
); then
    error "无法确认数据恢复状态，未修改配置或重启服务。"
    exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$PROJECT/.env.example" ]; then
        error "找不到 .env 和 .env.example"
        exit 1
    fi
    cp -- "$PROJECT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "原来没有 .env，已从模板创建。"
fi

if ! find_existing_key; then
    if [ -t 0 ]; then
        echo
        echo "未在当前配置或历史备份中找到 Qwen Key。"
        read -r -s -p "请输入 DashScope API Key（输入不会回显）：" INPUT_KEY
        echo
        KEY_SOURCE="本次安全输入"
    else
        error "未找到 QWEN_API_KEY，且当前不是交互终端。"
        error "请执行：sudo env QWEN_API_KEY='真实Key' bash $0"
        exit 2
    fi
fi
if ! is_real_key "$INPUT_KEY"; then
    error "QWEN_API_KEY 为空或仍是示例占位符，未修改配置。"
    exit 2
fi

mkdir -p -- "$BACKUP_ROOT"
BACKUP_ROOT_REAL="$(readlink -f "$BACKUP_ROOT")"
if [ -z "$BACKUP_ROOT_REAL" ] || [ "$BACKUP_ROOT_REAL" = "/" ]; then
    error "备份根目录不安全：$BACKUP_ROOT"
    exit 1
fi
BACKUP_DIR="$BACKUP_ROOT_REAL/ai-restore-$(date +%Y%m%d_%H%M%S)_${BASHPID}"
mkdir -- "$BACKUP_DIR"
cp -a -- "$ENV_FILE" "$BACKUP_DIR/.env.before-ai-restore"
chmod 600 "$BACKUP_DIR/.env.before-ai-restore"

set_env_value "$ENV_FILE" AI_MODE auto
set_env_value "$ENV_FILE" QWEN_BASE_URL \
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
set_env_value "$ENV_FILE" QWEN_MODEL "qwen3.8-flash"
set_env_value "$ENV_FILE" QWEN_API_KEY "$INPUT_KEY"
set_env_value "$ENV_FILE" AI_STRUCTURED_MODE "json_schema"
set_env_value "$ENV_FILE" AI_TIMEOUT_SECONDS "60"
chmod 600 "$ENV_FILE"

KEY_HINT="****${INPUT_KEY: -4}"
unset INPUT_KEY
success "配置已写入（来源：$KEY_SOURCE；Key：$KEY_HINT）"
echo "修改前配置备份：$BACKUP_DIR/.env.before-ai-restore"

systemctl restart "$SERVICE"
for attempt in {1..15}; do
    if systemctl is-active --quiet "$SERVICE" \
        && curl --fail --silent --connect-timeout 3 --max-time 10 \
            "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        success "服务重启和健康检查通过"
        break
    fi
    if [ "$attempt" -eq 15 ]; then
        error "服务健康检查失败，请查看：journalctl -u $SERVICE -n 100 --no-pager"
        exit 3
    fi
    sleep 2
done

echo
echo "正在调用 Qwen 做真实连接测试……"
if (cd "$PROJECT/backend" && "$VENV/bin/python" - <<'PY'
from app import config
from app.services.ai.adapter import test_qwen_connection

print(f"请求模式：{config.AI_MODE_REQUESTED}")
print(f"生效模式：{config.AI_MODE}")
print(f"模型：{config.QWEN_MODEL}")
print(f"Key：****{config.QWEN_API_KEY[-4:]}")
result = test_qwen_connection()
print(result.get("message", ""))
if not result.get("ok") or result.get("mode") != "qwen":
    raise SystemExit(1)
PY
); then
    success "真实 Qwen AI 已恢复并通过连接测试。"
    echo "请回到管理页刷新，运行模式应显示 Qwen，再点击“测试 AI 连接”复核。"
else
    error "配置已保存，但 Qwen 真实连接测试未通过。"
    error "请检查 Key 权限、DashScope 余额和服务器外网；应用仍会自动回退本地规则。"
    error "修改前配置保存在：$BACKUP_DIR/.env.before-ai-restore"
    exit 4
fi
