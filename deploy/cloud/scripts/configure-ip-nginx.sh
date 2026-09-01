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

backup="${vhost_config}.before-jx-$(date +%Y%m%d-%H%M%S).bak"
temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
sed \
  -e "s/203\.0\.113\.20/${public_ip}/g" \
  -e "s/198\.51\.100\.10/${allowed_ip}/g" \
  "${template}" > "${temporary}"

cp -a -- "${vhost_config}" "${backup}"
install -o root -g root -m 0644 "${temporary}" "${vhost_config}"
if ! "${nginx_binary}" -t; then
  cp -a -- "${backup}" "${vhost_config}"
  "${nginx_binary}" -t || true
  echo "新配置校验失败，已恢复原宝塔配置：${backup}" >&2
  exit 1
fi
"${nginx_service}" reload

echo "宝塔 IP 站点已反向代理到 127.0.0.1:1215。"
echo "仅允许 ${allowed_ip} 访问；原配置备份在 ${backup}。"
echo "请分别从白名单内网络和手机流量测试，手机流量应返回 403。"
