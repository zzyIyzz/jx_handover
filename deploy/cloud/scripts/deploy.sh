#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
env_file="${cloud_dir}/.env"

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

docker compose config --quiet
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
