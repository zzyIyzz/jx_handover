#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Execute one already scheduled restore with verification and explicit consent.
PROJECT="${PROJECT:-/www/wwwroot/jx_handover}"
SERVICE="${SERVICE:-jx-handover}"
PORT="${PORT:-8765}"
VENV="$PROJECT/.venv"

INFO_FILE=""
RESULT_FILE=""

success() { echo "[成功] $1"; }
warn() { echo "[警告] $1"; }
error() { echo "[错误] $1" >&2; }

cleanup() {
    [ -z "$INFO_FILE" ] || rm -f -- "$INFO_FILE"
    [ -z "$RESULT_FILE" ] || rm -f -- "$RESULT_FILE"
}
trap cleanup EXIT

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    error "请使用 root 权限执行：sudo bash $0"
    exit 1
fi
for command_name in curl systemctl journalctl; do
    command -v "$command_name" >/dev/null 2>&1 || {
        error "缺少命令：$command_name"
        exit 1
    }
done
[ -d "$PROJECT" ] || { error "项目目录不存在：$PROJECT"; exit 1; }
[ -x "$VENV/bin/python" ] || { error "Python 虚拟环境不存在：$VENV"; exit 1; }

INFO_FILE="$(mktemp /tmp/jx-restore-info.XXXXXX.json)"
(cd "$PROJECT/backend" && JX_RESTORE_INFO_FILE="$INFO_FILE" \
    "$VENV/bin/python" - <<'PY'
import json
import os
from pathlib import Path

from app import config
from app.services.backup import pending_restore_status, verify_full_backup

restore_dir = config.SNAPSHOT_DIR / "restore"
applying = restore_dir / "applying.json"
if applying.exists():
    raise SystemExit(
        f"检测到未完成或回滚失败的恢复：{applying}；必须人工核实，不能重复执行。"
    )
pending = pending_restore_status()
if pending is None:
    raise SystemExit("当前没有已安排的恢复任务。")
if pending.get("state") != "pending_restart":
    raise SystemExit("待恢复任务记录无效：" + json.dumps(pending, ensure_ascii=False))

backup_id = str(pending.get("backup_id") or "")
verified = verify_full_backup(backup_id)
expected_digest = str(pending.get("bundle_sha256") or "").lower()
actual_digest = str(verified.get("bundle_sha256") or "").lower()
if not expected_digest or expected_digest != actual_digest:
    raise SystemExit("恢复任务 SHA256 与当前备份不一致，拒绝重启。")
target = pending.get("target_data_root")
if target and Path(str(target)).expanduser().resolve() != config.USER_DATA_ROOT.resolve():
    raise SystemExit(
        f"恢复任务绑定目录 {target} 与当前数据根 {config.USER_DATA_ROOT.resolve()} 不一致。"
    )

payload = {
    "backup_id": backup_id,
    "requested_by": pending.get("requested_by"),
    "requested_at": pending.get("requested_at"),
    "data_root": str(config.USER_DATA_ROOT.resolve()),
    "database": str(config.DATABASE_PATH.resolve()),
    "bundle": verified.get("local_path"),
    "bundle_sha256": actual_digest,
    "verification": verified.get("verification"),
}
with open(os.environ["JX_RESTORE_INFO_FILE"], "w", encoding="utf-8") as stream:
    json.dump(payload, stream, ensure_ascii=False, indent=2)
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
)

BACKUP_ID="$(JX_RESTORE_INFO_FILE="$INFO_FILE" "$VENV/bin/python" - <<'PY'
import json
import os
print(json.load(open(os.environ["JX_RESTORE_INFO_FILE"], encoding="utf-8"))["backup_id"])
PY
)"

echo
echo "============================================================"
echo " 数据恢复执行确认"
echo "============================================================"
cat "$INFO_FILE"
echo
warn "恢复会把账号密码、交接班记录、导入原件和 Word 恢复到目标备份时间点。"
warn "恢复开始前会自动创建 pre-restore 完整备份，恢复后所有用户需重新登录。"

if [ -t 0 ]; then
    read -r -p "请输入目标备份编号确认恢复：" confirmation
else
    confirmation="${CONFIRM_RESTORE:-}"
fi
if [ "$confirmation" != "$BACKUP_ID" ]; then
    error "确认编号不一致，未重启服务，也没有执行恢复。"
    exit 2
fi

echo
echo "正在重启 $SERVICE，恢复将在 Web 服务启动前执行……"
if ! systemctl restart "$SERVICE"; then
    warn "首次启动返回失败；systemd 可能正在按策略重启，继续检查恢复结果。"
fi

health_ok=0
for attempt in {1..60}; do
    if systemctl is-active --quiet "$SERVICE" \
        && curl --fail --silent --max-time 5 \
            "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        health_ok=1
        break
    fi
    sleep 2
done

RESULT_FILE="$(mktemp /tmp/jx-restore-result.XXXXXX.json)"
set +e
(cd "$PROJECT/backend" && JX_EXPECTED_BACKUP_ID="$BACKUP_ID" \
    JX_RESTORE_RESULT_FILE="$RESULT_FILE" "$VENV/bin/python" - <<'PY'
import json
import os
import sqlite3

from app import config
from app.services.backup import last_restore_result, pending_restore_status

restore_dir = config.SNAPSHOT_DIR / "restore"
pending = pending_restore_status()
result = last_restore_result()
applying = restore_dir / "applying.json"
database_check = "missing"
if config.DATABASE_PATH.is_file():
    connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        database_check = str(row[0]) if row else "unknown"
    finally:
        connection.close()

payload = {
    "pending": pending,
    "last_result": result,
    "applying_exists": applying.exists(),
    "data_root": str(config.USER_DATA_ROOT.resolve()),
    "database_check": database_check,
}
with open(os.environ["JX_RESTORE_RESULT_FILE"], "w", encoding="utf-8") as stream:
    json.dump(payload, stream, ensure_ascii=False, indent=2)

expected = os.environ["JX_EXPECTED_BACKUP_ID"]
if applying.exists():
    raise SystemExit(4)
if pending is not None:
    raise SystemExit(5)
if not isinstance(result, dict) or result.get("backup_id") != expected:
    raise SystemExit(6)
if result.get("state") != "completed":
    raise SystemExit(3)
if database_check.lower() != "ok":
    raise SystemExit(7)
PY
)
result_code=$?
set -e

cat "$RESULT_FILE"
echo
if [ "$result_code" -eq 0 ] && [ "$health_ok" -eq 1 ]; then
    success "数据恢复完成，数据库完整性和服务健康检查均通过。"
    (cd "$PROJECT" && "$VENV/bin/python" backend/scripts/manage_admin.py --list)
    exit 0
fi

journalctl -u "$SERVICE" -n 160 --no-pager || true
case "$result_code" in
    3)
        error "恢复未完成，但自动回退可能已经成功；请查看上面的 last_result 和日志。"
        ;;
    4)
        error "存在 applying.json，恢复中断或回退失败；停止操作并保留现场。"
        ;;
    5)
        error "恢复任务仍处于 pending 状态，服务没有执行该任务。"
        ;;
    6)
        error "恢复结果与本次目标备份编号不一致。"
        ;;
    7)
        error "恢复后的数据库 quick_check 未通过。"
        ;;
    *)
        error "服务或恢复结果检查失败（结果码：$result_code，健康检查：$health_ok）。"
        ;;
esac
exit 3
