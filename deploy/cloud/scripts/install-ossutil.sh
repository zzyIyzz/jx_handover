#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行此脚本。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "当前安装脚本只锁定并校验 Linux x86_64 包；请按阿里云官方页面选择对应架构。" >&2
  exit 1
fi
for command_name in curl unzip sha256sum find install; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少安装依赖：${command_name}" >&2
    exit 1
  fi
done

version="2.4.0"
archive="ossutil-${version}-linux-amd64.zip"
expected_sha256="85edf66b2fb7238f5c7e25cab820cf29312319fe4935b7c86a6b8485eb434f3c"
download_url="https://gosspublic.alicdn.com/ossutil/v2/${version}/${archive}"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "${work_dir}"' EXIT

curl --fail --location --output "${work_dir}/${archive}" "${download_url}"
echo "${expected_sha256}  ${work_dir}/${archive}" | sha256sum --check --status
unzip -q "${work_dir}/${archive}" -d "${work_dir}/unpacked"
binary="$(find "${work_dir}/unpacked" -type f -name ossutil -print -quit)"
if [[ -z "${binary}" ]]; then
  echo "安装包中未找到 ossutil。" >&2
  exit 1
fi
install -o root -g root -m 0755 "${binary}" /usr/local/bin/ossutil
/usr/local/bin/ossutil --version
echo "ossutil ${version} 已完成 SHA256 校验并安装。"
