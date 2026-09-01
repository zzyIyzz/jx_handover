#!/bin/sh
set -eu

data_root="${JX_HANDOVER_DATA_DIR:-/var/lib/jx-handover}"
if [ ! -d "${data_root}" ] || [ ! -w "${data_root}" ]; then
    echo "云端正式数据目录不存在或容器 UID 10001 无写权限：${data_root}" >&2
    exit 1
fi
filesystem_type="$(stat -f -c %T "${data_root}")"
case "${filesystem_type}" in
    nfs|nfs4|cifs|smb*|fuse*|9p)
        echo "检测到网络/用户态文件系统 ${filesystem_type}；SQLite 正式库必须放在 ECS 本地 ESSD。" >&2
        exit 1
        ;;
esac

python /opt/jx-handover/backend/scripts/cloud_preflight.py

exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8765 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips="*" \
    --no-server-header
