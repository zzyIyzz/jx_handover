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

: "${JX_CLOUD_COMPOSE_DIR:?未设置 JX_CLOUD_COMPOSE_DIR}"
: "${JX_HOST_DATA_DIR:?未设置 JX_HOST_DATA_DIR}"
: "${JX_OSS_URI:?未设置 JX_OSS_URI}"
: "${JX_OSSUTIL_CONFIG:?未设置 JX_OSSUTIL_CONFIG}"
ossutil_bin="${JX_OSSUTIL_BIN:-/usr/local/bin/ossutil}"
backup_dir="${JX_HOST_DATA_DIR}/snapshots/full_backups"
if [[ ! -x "${ossutil_bin}" ]]; then
  echo "ossutil 不存在或不可执行：${ossutil_bin}" >&2
  exit 1
fi

cd -- "${JX_CLOUD_COMPOSE_DIR}"
docker compose exec -T app \
  python /opt/jx-handover/backend/scripts/cloud_backup.py daily

if [[ ! -d "${backup_dir}" ]]; then
  echo "本地完整备份目录不存在：${backup_dir}" >&2
  exit 1
fi

uploaded=0
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
done < <(find "${backup_dir}" -maxdepth 1 -type f -name 'jx-handover-backup-*.zip' -print0)

echo "OSS 异地备份完成：检查并同步 ${uploaded} 组 ZIP + JSON；目标 ${JX_OSS_URI%/}/。"
