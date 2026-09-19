#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One command after the operator has mounted a separate ESSD at /data.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prepare_script="$script_dir/prepare-data-disk-v0.5.4.sh"
update_script="$script_dir/update_jx_handover.sh"
restore_ai_script="$script_dir/restore-ai.sh"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "[错误] 请使用 root 权限执行：sudo bash $0" >&2
    exit 1
fi
for required in "$prepare_script" "$update_script" "$restore_ai_script"; do
    if [ ! -f "$required" ]; then
        echo "[错误] 一键升级目录不完整，缺少：$required" >&2
        exit 1
    fi
done

echo "============================================================"
echo " 江西片区智能交接班系统 V0.5.4 - 数据盘迁移与升级"
echo "============================================================"
echo "本脚本不会格式化或挂载磁盘。"
echo "要求：新的独立 ESSD 已由管理员确认并挂载到 /data。"
echo

install -o root -g root -m 700 \
    "$prepare_script" /root/prepare-data-disk-v0.5.4.sh
install -o root -g root -m 700 \
    "$update_script" /root/update_jx_handover.sh
install -o root -g root -m 700 \
    "$restore_ai_script" /root/restore-ai.sh

echo "[1/2] 准备独立数据盘并迁移现有业务数据"
bash /root/prepare-data-disk-v0.5.4.sh

echo
echo "[2/2] 更新到 release/v0.5.4 并执行完整验收"
exec bash /root/update_jx_handover.sh
