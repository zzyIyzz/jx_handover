#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]]; then
  echo "用法：$0 备份编号，例如 20260901-021500-a1b2c3d4" >&2
  exit 1
fi
backup_id="$1"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
backup_env="${JX_OSS_BACKUP_ENV:-${cloud_dir}/oss-backup.env}"
if [[ ! -f "${backup_env}" ]]; then
  echo "缺少 ${backup_env}。" >&2
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
base_name="jx-handover-backup-${backup_id}"
bundle="${backup_dir}/${base_name}.zip"
manifest="${backup_dir}/${base_name}.json"
remote_root="${JX_OSS_URI%/}"

if [[ -e "${bundle}" || -e "${manifest}" ]]; then
  echo "本地已存在同编号文件；为避免覆盖，已停止。请先在网页中执行重新校验。" >&2
  exit 1
fi
install -d -o 10001 -g 10001 -m 0750 "${backup_dir}"
work_dir="$(mktemp -d --tmpdir="${backup_dir}" .oss-restore-XXXXXXXX)"
trap 'rm -rf -- "${work_dir}"' EXIT

"${ossutil_bin}" -c "${JX_OSSUTIL_CONFIG}" --ignore-env-var \
  --retry-times 10 cp "${remote_root}/${base_name}.zip" "${work_dir}/${base_name}.zip" --force
"${ossutil_bin}" -c "${JX_OSSUTIL_CONFIG}" --ignore-env-var \
  --retry-times 10 cp "${remote_root}/${base_name}.json" "${work_dir}/${base_name}.json" --force
chown 10001:10001 "${work_dir}/${base_name}.zip" "${work_dir}/${base_name}.json"
chmod 0640 "${work_dir}/${base_name}.zip" "${work_dir}/${base_name}.json"
mv -- "${work_dir}/${base_name}.zip" "${bundle}"
mv -- "${work_dir}/${base_name}.json" "${manifest}"

cd -- "${JX_CLOUD_COMPOSE_DIR}"
docker compose exec -T app \
  python /opt/jx-handover/backend/scripts/cloud_backup.py verify "${backup_id}"
echo "备份已下载并通过 ZIP、SHA256 与 SQLite 校验。"
echo "下一步：在网页系统管理中安排恢复，然后执行 docker compose restart app。"
