#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

data_device="${DATA_DEVICE:-}"
data_mount="${DATA_MOUNT:-/data}"
allow_system_disk="${ALLOW_SYSTEM_DISK:-1}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash deploy/cloud/scripts/prepare-host.sh" >&2
  exit 1
fi

for command_name in findmnt readlink; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "缺少命令：${command_name}" >&2
    exit 1
  }
done
mkdir -p -- "${data_mount}"
mount_source="$(findmnt -n -o SOURCE --target "${data_mount}" 2>/dev/null || true)"
mount_target="$(findmnt -n -o TARGET --target "${data_mount}" 2>/dev/null || true)"
mount_source="${mount_source%%[*}"
source_device="$(readlink -f "${mount_source}" 2>/dev/null || true)"
root_source="$(findmnt -n -o SOURCE --target / 2>/dev/null || true)"
root_source="${root_source%%[*}"
root_device="$(readlink -f "${root_source}" 2>/dev/null || true)"
if [[ -z "${source_device}" || ! -b "${source_device}" ]]; then
  echo "无法识别 ${data_mount} 对应的持久化设备。" >&2
  exit 1
fi
if [[ "${source_device}" == "${root_device}" ]]; then
  if [[ "${allow_system_disk}" != "1" ]]; then
    echo "${data_mount} 使用系统根分区；当前配置要求独立数据盘。" >&2
    exit 1
  fi
  echo "提示：当前使用系统盘持久化，请确保 OSS 异地备份正常。"
elif [[ "${mount_target}" != "${data_mount}" ]]; then
  echo "${data_mount} 位于其他挂载层级，无法确认数据盘边界。" >&2
  exit 1
fi
if [[ -n "${data_device}" \
  && "${source_device}" != "$(readlink -f "${data_device}" 2>/dev/null || true)" ]]; then
  echo "${data_mount} 来源为 ${source_device}，不是指定设备 ${data_device}。" >&2
  exit 1
fi
data_device="${source_device}"

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
  echo "已按 ${profile} 模板创建 ${env_file}，请先填写实际 HTTPS 地址、会话密钥、管理员和 Qwen Key；人员首次密码为 aaaa0000*。"
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
  data_root="/data/jx-handover/data"
fi
if [[ "${data_root}" != /* || "${data_root}" == "/" ]]; then
  echo "JX_HOST_DATA_DIR 必须是 ECS 本地磁盘上的专用绝对目录，不能是 /。" >&2
  exit 1
fi
if [[ "${data_root}" != "${data_mount}"/* ]]; then
  echo "JX_HOST_DATA_DIR 必须位于数据盘 ${data_mount} 下：${data_root}" >&2
  exit 1
fi
legacy_root="/www/jx-handover/data"
if [[ "${data_root}" != "${legacy_root}" \
  && -f "${legacy_root}/data/handover.db" \
  && ! -f "${data_root}/data/handover.db" ]]; then
  echo "发现旧 Docker 正式数据库，但数据盘目标尚未迁移：${legacy_root}" >&2
  echo "请先停止容器并按数据盘迁移文档复制数据，脚本不会用空目录覆盖旧业务库。" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0750 "${data_root}"
install -d -o root -g root -m 0700 "${data_mount}/jx-handover/config"
echo "数据目录已准备：${data_root}（容器 UID/GID 10001，权限 0750）。"
echo "下一步：编辑 ${env_file}，然后执行 deploy/cloud/scripts/deploy.sh。"
