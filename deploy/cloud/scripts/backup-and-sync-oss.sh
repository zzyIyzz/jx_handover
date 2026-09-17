#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
backup_env="${JX_OSS_BACKUP_ENV:-${cloud_dir}/oss-backup.env}"
if [[ ! -f "${backup_env}" ]]; then
  echo "缺少 ${backup_env}；请从 oss-backup.env.example 复制并填写。" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${backup_env}"
set +a

: "${JX_HOST_DATA_DIR:?未设置 JX_HOST_DATA_DIR}"
: "${JX_OSS_URI:?未设置 JX_OSS_URI}"
: "${JX_OSSUTIL_CONFIG:?未设置 JX_OSSUTIL_CONFIG}"
ossutil_bin="${JX_OSSUTIL_BIN:-/usr/local/bin/ossutil}"
backup_dir="${JX_HOST_DATA_DIR}/snapshots/full_backups"
receipt_helper="${script_dir}/oss-sync-receipt.py"
command -v python3 >/dev/null || { echo "请安装 python3 以生成同步状态记录。" >&2; exit 1; }
command -v flock >/dev/null || { echo "请安装 util-linux（flock）。" >&2; exit 1; }
[[ -d "${JX_HOST_DATA_DIR}/snapshots" ]] || { echo "数据目录不正确，未发现 snapshots。" >&2; exit 1; }
exec 9>"${JX_HOST_DATA_DIR}/snapshots/.oss-sync.lock"
flock -n 9 || { echo "已有 OSS 同步任务运行，本次跳过。"; exit 0; }
uploaded=0
report_exit() {
  result=$?
  if [[ "${result}" -ne 0 ]]; then
    python3 "${receipt_helper}" failed --count "${uploaded}" || true
  fi
}
trap report_exit EXIT
python3 "${receipt_helper}" running
if [[ ! -x "${ossutil_bin}" ]]; then
  echo "ossutil 不存在或不可执行：${ossutil_bin}" >&2
  exit 1
fi

# Docker 部署：先让容器内应用生成当日备份。
# systemd 部署（JX_CLOUD_COMPOSE_DIR 留空）：应用内置调度器已自动生成每日备份。
if [[ -n "${JX_CLOUD_COMPOSE_DIR:-}" ]]; then
  cd -- "${JX_CLOUD_COMPOSE_DIR}"
  docker compose exec -T app \
    python /opt/jx-handover/backend/scripts/cloud_backup.py daily
fi

if [[ ! -d "${backup_dir}" ]]; then
  echo "本地完整备份目录不存在：${backup_dir}" >&2
  exit 1
fi

# Refuse an empty, corrupt or stale backup set instead of reporting false success.
python3 "${receipt_helper}" check
while IFS= read -r -d '' bundle; do
  manifest="${bundle%.zip}.json"
  if [[ ! -f "${manifest}" ]]; then
    echo "跳过缺少清单的备份：${bundle}" >&2
    continue
  fi
  bundle_name="$(basename -- "${bundle}")"
  manifest_name="$(basename -- "${manifest}")"
  remote_root="${JX_OSS_URI%/}"
  "${ossutil_bin}" -c "${JX_OSSUTIL_CONFIG}" --ignore-env-var \
    --retry-times 10 cp "${bundle}" "${remote_root}/${bundle_name}" --update --force
  # 清单最后上传；看到清单时，对应 ZIP 已经完成上传。
  "${ossutil_bin}" -c "${JX_OSSUTIL_CONFIG}" --ignore-env-var \
    --retry-times 10 cp "${manifest}" "${remote_root}/${manifest_name}" --update --force
  uploaded=$((uploaded + 1))
  python3 "${receipt_helper}" uploaded --bundle "${bundle}"
done < <(find "${backup_dir}" -maxdepth 1 -type f -name 'jx-handover-backup-*.zip' -print0)

python3 "${receipt_helper}" success --count "${uploaded}"
echo "OSS 异地备份完成：检查并同步 ${uploaded} 组 ZIP + JSON；目标 ${JX_OSS_URI%/}/。"
