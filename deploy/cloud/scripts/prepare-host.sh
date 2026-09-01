#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash deploy/cloud/scripts/prepare-host.sh" >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
env_file="${cloud_dir}/.env"
profile="${1:---domain}"
case "${profile}" in
  --domain)
    env_template="${cloud_dir}/.env.example"
    ;;
  --ip)
    env_template="${cloud_dir}/.env.ip.example"
    ;;
  *)
    echo "用法：sudo bash deploy/cloud/scripts/prepare-host.sh [--domain|--ip]" >&2
    exit 1
    ;;
esac

if [[ ! -f "${env_file}" ]]; then
  cp -- "${env_template}" "${env_file}"
  chmod 0600 "${env_file}"
  echo "已按 ${profile} 模板创建 ${env_file}，请先填写实际 HTTPS 地址、访问口令、会话密钥、管理员和 Qwen Key。"
else
  chmod 0600 "${env_file}"
  echo "保留已有 ${env_file}，没有覆盖。"
fi

data_root="$(sed -n 's/^JX_HOST_DATA_DIR=//p' "${env_file}" | tail -n 1 | tr -d '\r')"
data_root="${data_root%\"}"
data_root="${data_root#\"}"
data_root="${data_root%\'}"
data_root="${data_root#\'}"
if [[ -z "${data_root}" ]]; then
  data_root="/www/jx-handover/data"
fi
if [[ "${data_root}" != /* || "${data_root}" == "/" ]]; then
  echo "JX_HOST_DATA_DIR 必须是 ECS 本地磁盘上的专用绝对目录，不能是 /。" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0750 "${data_root}"
install -d -o root -g root -m 0700 /www/jx-handover/config
echo "数据目录已准备：${data_root}（容器 UID/GID 10001，权限 0750）。"
echo "下一步：编辑 ${env_file}，然后执行 deploy/cloud/scripts/deploy.sh。"
