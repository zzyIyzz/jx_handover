#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash deploy/cloud/scripts/configure-ip-nginx.sh 公网IPv4 允许访问的公网IPv4" >&2
  exit 1
fi
if [[ $# -ne 2 ]]; then
  echo "用法：sudo bash deploy/cloud/scripts/configure-ip-nginx.sh 公网IPv4 允许访问的公网IPv4" >&2
  exit 1
fi

valid_ipv4() {
  local value="$1" part
  local -a parts
  [[ "${value}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS='.' read -r -a parts <<< "${value}"
  for part in "${parts[@]}"; do
    (( 10#${part} <= 255 )) || return 1
  done
}

public_ip="$1"
allowed_ip="$2"
if ! valid_ipv4 "${public_ip}" || ! valid_ipv4 "${allowed_ip}"; then
  echo "公网 IPv4 格式不正确。第一轮配置只接受一个精确 IPv4，不接受域名或任意来源。" >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_dir="$(cd -- "${script_dir}/.." && pwd)"
template="${cloud_dir}/nginx/jx-handover-ip-server.conf.example"
vhost_config="/www/server/panel/vhost/nginx/${public_ip}.conf"
cert_dir="/www/server/panel/vhost/cert/${public_ip}"
nginx_binary="/www/server/nginx/sbin/nginx"
nginx_service="/etc/init.d/nginx"

for required_file in "${template}" "${vhost_config}" \
    "${cert_dir}/privkey.pem" "${cert_dir}/fullchain.pem"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "缺少文件：${required_file}" >&2
    echo "请先创建宝塔 IP 站点并运行 install-ip-certificate.sh。" >&2
    exit 1
  fi
done
if [[ ! -x "${nginx_binary}" || ! -x "${nginx_service}" ]]; then
  echo "未检测到宝塔 Nginx。" >&2
  exit 1
fi
if ! command -v ss >/dev/null 2>&1; then
  echo "缺少 ss 命令，无法安全检查公网端口 1215。请先安装 iproute2。" >&2
  exit 1
fi

port_1215_listeners() {
  ss -H -lntp 'sport = :1215' 2>/dev/null || true
}

listener_has_non_nginx_process() {
  local listeners="$1"
  grep -vi 'nginx' <<< "${listeners}" | grep -q '[^[:space:]]'
}

listener_is_public_nginx_only() {
  local listeners="$1"
  [[ -n "${listeners}" ]] || return 1
  if listener_has_non_nginx_process "${listeners}"; then
    return 1
  fi
  grep -Eq '[[:space:]](0\.0\.0\.0|\*|\[::\]|::):1215[[:space:]]' <<< "${listeners}"
}

existing_listener="$(port_1215_listeners)"
if [[ -n "${existing_listener}" ]] && listener_has_non_nginx_process "${existing_listener}"; then
  echo "公网端口 1215 已被其他程序占用，未修改宝塔配置，也不会结束占用进程：" >&2
  printf '%s\n' "${existing_listener}" >&2
  echo "请先确认并处理占用，再重新执行本脚本。内部 Docker 应只使用 127.0.0.1:8765。" >&2
  exit 1
fi

backup="${vhost_config}.before-jx-$(date +%Y%m%d-%H%M%S).bak"
temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
sed \
  -e "s/203\.0\.113\.20/${public_ip}/g" \
  -e "s/198\.51\.100\.10/${allowed_ip}/g" \
  "${template}" > "${temporary}"

cp -a -- "${vhost_config}" "${backup}"
install -o root -g root -m 0644 "${temporary}" "${vhost_config}"

restore_previous_config() {
  if ! cp -a -- "${backup}" "${vhost_config}"; then
    echo "无法把原宝塔配置复制回 ${vhost_config}，必须立即人工处理。" >&2
    return 1
  fi
  if ! "${nginx_binary}" -t; then
    echo "原文件已恢复，但 Nginx 配置校验仍失败，必须人工检查。" >&2
    return 1
  fi
  if ! "${nginx_service}" reload; then
    echo "原文件已恢复，但 Nginx 恢复重载失败，必须人工执行 nginx -t 和 reload。" >&2
    return 1
  fi
}

fail_and_restore() {
  local reason="$1"
  if restore_previous_config; then
    echo "${reason}，已恢复原宝塔配置：${backup}" >&2
  else
    echo "${reason}；原文件备份位于 ${backup}，但 Nginx 未确认恢复运行，请立即人工处理。" >&2
  fi
  exit 1
}

if ! "${nginx_binary}" -t; then
  fail_and_restore "新配置校验失败"
fi
if ! "${nginx_service}" reload; then
  fail_and_restore "Nginx 重载失败"
fi

listener_after_reload=""
for _ in {1..20}; do
  listener_after_reload="$(port_1215_listeners)"
  if listener_is_public_nginx_only "${listener_after_reload}"; then
    break
  fi
  sleep 0.25
done
if ! listener_is_public_nginx_only "${listener_after_reload}"; then
  [[ -n "${listener_after_reload}" ]] && printf '%s\n' "${listener_after_reload}" >&2
  fail_and_restore "Nginx 重载后没有成功监听公网端口 1215，或检测到非 Nginx 监听者"
fi

echo "宝塔已在公网 HTTPS 1215 监听，并反向代理到内部 127.0.0.1:8765。"
echo "仅允许 ${allowed_ip} 访问；原配置备份在 ${backup}。"
echo "请分别从白名单内网络和手机流量测试，手机流量应返回 403。"
