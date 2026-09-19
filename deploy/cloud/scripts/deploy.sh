#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
env_file="${cloud_dir}/.env"
data_device="${DATA_DEVICE:-}"
data_mount="${DATA_MOUNT:-/data}"
persisted_env="${PERSISTED_ENV_FILE:-/data/jx-handover/config/docker.env}"
allow_system_disk="${ALLOW_SYSTEM_DISK:-1}"

read_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "${env_file}" | tail -n 1 | tr -d '\r' \
    | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local temporary=""
  temporary="$(mktemp "${env_file}.tmp.XXXXXX")"
  JX_ENV_REPLACEMENT="${value}" awk -v wanted="${key}" '
    BEGIN { done=0; replacement=ENVIRON["JX_ENV_REPLACEMENT"] }
    $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
      if (!done) print wanted "=" replacement
      done=1
      next
    }
    { print }
    END { if (!done) print wanted "=" replacement }
  ' "${env_file}" > "${temporary}"
  chmod 600 "${temporary}"
  mv -f -- "${temporary}" "${env_file}"
}

if [[ ! -f "${env_file}" ]]; then
  echo "缺少 ${env_file}；请先运行 prepare-host.sh 并完成配置。" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker。请先在宝塔软件商店安装 Docker/Compose 管理器。" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "未找到 curl，无法执行健康检查。请先安装 curl。" >&2
  exit 1
fi
for command_name in python3 readlink; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "未找到 ${command_name}，无法完成安全配置。" >&2
    exit 1
  fi
done
if ! command -v findmnt >/dev/null 2>&1; then
  echo "未找到 findmnt，无法确认独立数据盘挂载。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "未找到 Docker Compose v2。" >&2
  exit 1
fi

cd -- "${cloud_dir}"

data_root="$(sed -n 's/^JX_HOST_DATA_DIR=//p' "${env_file}" | tail -n 1 | tr -d '\r')"
data_root="${data_root%\"}"
data_root="${data_root#\"}"
data_root="${data_root%\'}"
data_root="${data_root#\'}"
if [[ -z "${data_root}" || ! -d "${data_root}" ]]; then
  echo "正式数据目录不存在：${data_root:-（空）}" >&2
  echo "修改 JX_HOST_DATA_DIR 后，请重新执行 sudo bash deploy/cloud/scripts/prepare-host.sh。" >&2
  exit 1
fi
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
if [[ "${data_root}" != "${data_mount}"/* ]]; then
  echo "JX_HOST_DATA_DIR 必须位于数据盘 ${data_mount} 下。" >&2
  exit 1
fi
if [[ "${persisted_env}" != "${data_mount}"/* ]]; then
  echo "JX_CONTAINER_ENV_FILE 必须位于数据盘 ${data_mount} 下。" >&2
  exit 1
fi
legacy_root="/www/jx-handover/data"
if [[ "${data_root}" != "${legacy_root}" \
  && -f "${legacy_root}/data/handover.db" \
  && ! -f "${data_root}/data/handover.db" ]]; then
  echo "检测到旧业务数据库但数据盘目标为空，拒绝启动新空库。" >&2
  exit 1
fi

set_env_value JX_ADMIN_NAMES "周智源"
set_env_value JX_DEFAULT_ADMIN_NAMES "周智源"
set_env_value JX_CONTAINER_ENV_FILE "${persisted_env}"
session_secret="$(read_env_value JX_SESSION_SECRET)"
if [[ ${#session_secret} -lt 32 || "${session_secret}" == *"请替换"* ]]; then
  session_secret="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
  )"
  set_env_value JX_SESSION_SECRET "${session_secret}"
  echo "原会话密钥缺失或无效，已生成随机密钥；现有浏览器需重新登录。"
fi
install -d -o root -g root -m 0700 "$(dirname "${persisted_env}")"
temporary_env="$(mktemp "$(dirname "${persisted_env}")/.docker-env.XXXXXX")"
cp -- "${env_file}" "${temporary_env}"
chmod 600 "${temporary_env}"
mv -f -- "${temporary_env}" "${persisted_env}"

docker compose config --quiet

if command -v ss >/dev/null 2>&1 \
    && ss -H -lnt 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:8765[[:space:]]'; then
  running_app="$(docker compose ps --status running -q app 2>/dev/null || true)"
  if [[ -z "${running_app}" ]]; then
    echo "ECS 本机内部端口 8765 已被其他程序占用；未启动容器，也不会结束占用进程。" >&2
    echo "请执行 ss -lntp | grep 8765 查看占用程序。" >&2
    exit 1
  fi
fi

if command -v ss >/dev/null 2>&1 \
    && ss -H -lnt 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:1215[[:space:]]'; then
  echo "提示：公网端口 1215 已被占用；该端口预留给宝塔 Nginx HTTPS 反向代理。" >&2
  echo "若占用程序不是 Nginx，请先释放 1215；脚本只提示，不会结束占用进程。" >&2
fi

docker compose build --pull
docker compose up -d --remove-orphans

public_url="$(sed -n 's/^JX_PUBLIC_URL=//p' "${env_file}" | tail -n 1 | tr -d '\r')"
public_url="${public_url%\"}"
public_url="${public_url#\"}"
public_url="${public_url%\'}"
public_url="${public_url#\'}"
public_host="${public_url#*://}"
public_host="${public_host%%/*}"
if [[ -z "${public_host}" || "${public_host}" == "${public_url}" ]]; then
  echo "无法从 JX_PUBLIC_URL 取得健康检查域名；请填写完整 HTTPS 地址。" >&2
  exit 1
fi
health_url="http://127.0.0.1:8765/api/health"
for _attempt in {1..60}; do
  if curl --fail --silent --show-error --max-time 5 \
      --header "Host: ${public_host}" "${health_url}" >/dev/null; then
    docker compose ps
    echo "应用已通过本机健康检查。请继续在宝塔配置 HTTPS 反向代理和访问白名单。"
    exit 0
  fi
  sleep 2
done

docker compose ps
docker compose logs --tail 120 app
echo "应用未在规定时间内通过健康检查，请根据上方日志修正配置。" >&2
exit 1
