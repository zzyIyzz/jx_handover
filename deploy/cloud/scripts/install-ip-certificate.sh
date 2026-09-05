#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash deploy/cloud/scripts/install-ip-certificate.sh 公网IPv4" >&2
  exit 1
fi
if [[ $# -ne 1 ]]; then
  echo "用法：sudo bash deploy/cloud/scripts/install-ip-certificate.sh 公网IPv4" >&2
  exit 1
fi

public_ip="$1"
if [[ ! "${public_ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "公网 IPv4 格式不正确。" >&2
  exit 1
fi
IFS='.' read -r octet1 octet2 octet3 octet4 <<< "${public_ip}"
for octet in "${octet1}" "${octet2}" "${octet3}" "${octet4}"; do
  if (( 10#${octet} > 255 )); then
    echo "公网 IPv4 格式不正确。" >&2
    exit 1
  fi
done
octet1=$((10#${octet1}))
octet2=$((10#${octet2}))
octet3=$((10#${octet3}))
octet4=$((10#${octet4}))
if (( octet1 == 0 || octet1 == 10 || octet1 == 127 || octet1 >= 224 )) \
    || (( octet1 == 169 && octet2 == 254 )) \
    || (( octet1 == 172 && octet2 >= 16 && octet2 <= 31 )) \
    || (( octet1 == 192 && octet2 == 168 )); then
  echo "必须填写 ECS 的真实公网 IPv4，不能使用内网、回环、链路本地或组播地址。" >&2
  exit 1
fi

site_root="/www/wwwroot/${public_ip}"
cert_dir="/www/server/panel/vhost/cert/${public_ip}"
nginx_binary="/www/server/nginx/sbin/nginx"
nginx_service="/etc/init.d/nginx"
if [[ ! -d "${site_root}" ]]; then
  echo "宝塔 IP 站点目录不存在：${site_root}" >&2
  echo "请先在宝塔“网站”中创建以公网 IP 命名的站点，并保留 HTTP 80 访问。" >&2
  exit 1
fi
if [[ ! -x "${nginx_binary}" || ! -x "${nginx_service}" ]]; then
  echo "未检测到宝塔 Nginx；请先在宝塔软件商店安装并启动 Nginx。" >&2
  exit 1
fi

if command -v acme.sh >/dev/null 2>&1; then
  acme_bin="$(command -v acme.sh)"
elif [[ -x /root/.acme.sh/acme.sh ]]; then
  acme_bin="/root/.acme.sh/acme.sh"
else
  echo "未找到 acme.sh。请先按宝塔官方 IP 证书教程安装或更新 acme.sh。" >&2
  exit 1
fi

install -d -o root -g root -m 0750 "${cert_dir}"
"${acme_bin}" --issue \
  --server letsencrypt \
  --cert-profile shortlived \
  --days 3 \
  -d "${public_ip}" \
  --webroot "${site_root}"

reload_command="${nginx_binary} -t && ${nginx_service} reload"
"${acme_bin}" --install-cert -d "${public_ip}" \
  --key-file "${cert_dir}/privkey.pem" \
  --fullchain-file "${cert_dir}/fullchain.pem" \
  --reloadcmd "${reload_command}"
"${acme_bin}" --install-cronjob

if [[ ! -s "${cert_dir}/privkey.pem" || ! -s "${cert_dir}/fullchain.pem" ]]; then
  echo "证书文件未正确写入 ${cert_dir}。" >&2
  exit 1
fi
if ! crontab -l 2>/dev/null | grep -q 'acme.sh.*--cron'; then
  echo "警告：没有检测到 acme.sh 自动续签任务。IP 证书约 6 天到期，禁止继续正式上线。" >&2
  exit 1
fi

echo "公网 IP 证书已安装：${cert_dir}。"
echo "已检测到 acme.sh 自动续签任务；请继续套用宝塔 IP 站点 Nginx 示例并测试 HTTPS。"
