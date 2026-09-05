# 江西片区智能交接班 V0.5.1 无域名公网 IP 部署说明

## 1. 适用范围

本说明用于以下现场条件：

- 已有阿里云 ECS 和固定公网 IPv4；
- 已安装宝塔 Linux 面板；
- 暂时没有域名；
- 只有系统盘，先使用 `/www/jx-handover/data`；
- 公网 HTTPS 固定使用 `1215`；Docker/FastAPI 内部仍使用 `127.0.0.1:8765`。

Let’s Encrypt 公网 IP 证书是约 6 天有效的短期证书，必须依赖 `acme.sh` 每日任务自动续签。它适合先完成内部试用；取得公司域名后，仍建议迁移到正式子域名。官方依据：

- <https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability.html>
- <https://docs.bt.cn/practical-tutorials/acme-sh-panel-ssl>

## 2. 部署前的四条边界

1. 公网 IPv4 必须是 ECS 当前固定 EIP；不要使用内网 IP、NAS 地址或会变化的临时地址。
2. 阿里云安全组和宝塔“安全/系统防火墙”都只向批准来源开放 TCP 1215；绝不开放内部 TCP 8765。
3. TCP 80 必须长期允许 ACME HTTP-01 校验，否则约 6 天后证书会失效。
4. TCP 1215 第一轮只允许部署管理员当前网络的公网出口 IPv4；个人姓名密码已经启用，但在没有 MFA 和公网开放验收前仍不能向任意来源开放。

建议安全组：

| 端口 | 来源 | 用途 |
|---|---|---|
| 22 | 管理员公网 IPv4/32 | SSH |
| 宝塔实际端口 | 管理员公网 IPv4/32 | 面板管理 |
| 80 | `0.0.0.0/0` | IP 证书申请和自动续签 |
| 1215 | 管理员或单位出口公网 IPv4/32 | 交接班 HTTPS 公网入口 |
| 8765 | 不创建规则 | 仅 ECS 本机 Nginx 访问 Docker |

安全组放行后，还要在宝塔“安全/系统防火墙”中按相同来源放行 1215；如果其中任一层未放行，浏览器都会超时。宝塔和阿里云两处都不要添加 8765 公网规则。

## 3. 从 GitHub 拉取程序

仓库设为 Private 后，先给 ECS 配置只读 Deploy Key 或其他公司批准的 GitHub 认证方式，然后执行：

```bash
mkdir -p /www/jx-handover
cd /www/jx-handover
git clone git@github.com:zzyIyzz/jx_handover.git app
cd app
git switch codex/v0.5.1-account-login
```

最终必须直接存在：

```text
/www/jx-handover/app/backend
/www/jx-handover/app/frontend
/www/jx-handover/app/resources
/www/jx-handover/app/deploy
```

不要把正式数据放在源码目录中。后续更新使用：

```bash
cd /www/jx-handover/app
git pull --ff-only
```

## 4. 检查服务器

在宝塔终端执行：

```bash
uname -m
docker --version
docker compose version
df -hT / /www
/www/server/nginx/sbin/nginx -v
ss -lntp | grep -E ':(80|1215|8765)\b' || true
```

要求：

- 架构与部署包一致，当前脚本按 Linux x86_64 准备；
- Docker 和 Compose V2 可用；
- `/www` 有足够空间；
- 内部 8765 空闲，或者已经由本系统当前容器占用；
- 8765 被其他程序占用时，部署脚本会停止但不会结束该程序；
- 公网 1215 在配置 Nginx 前应空闲，配置后只能由宝塔 Nginx 监听；脚本发现其他程序占用时不会强制结束进程。

## 5. 在宝塔创建公网 IP 站点

打开“网站”，创建新站点：

- 域名：填写 ECS 真实公网 IPv4；
- 根目录：`/www/wwwroot/真实公网IPv4`；
- PHP：纯静态；
- 数据库：不创建；
- FTP：不创建。

创建后先通过 HTTP 打开 `http://公网IPv4`，确认 80 端口可以到达默认页面。证书申请前不要启用强制 HTTPS，也不要提前覆盖 Nginx 配置。

## 6. 创建无域名配置

在终端执行：

```bash
cd /www/jx-handover/app
sudo bash deploy/cloud/scripts/prepare-host.sh --ip
```

脚本会从 `.env.ip.example` 创建权限为 `0600` 的 `deploy/cloud/.env`。编辑该文件并至少替换：

```dotenv
JX_HOST_DATA_DIR=/www/jx-handover/data
JX_PUBLIC_URL=https://你的真实公网IPv4:1215
JX_TRUSTED_HOSTS=你的真实公网IPv4,127.0.0.1,localhost
JX_AUTH_REQUIRED=1
JX_COOKIE_SECURE=1
JX_ACCOUNT_LOGIN_ENABLED=1
JX_INITIAL_ACCOUNT_PASSWORD=aaaa0000*
JX_ACCESS_CODE=
JX_SESSION_SECRET=至少32位随机会话密钥
JX_ADMIN_NAMES=实际管理员姓名
QWEN_API_KEY=你自己的DashScope_API_Key
```

生成随机值：

```bash
openssl rand -hex 32
```

只需生成会话签名密钥；人员第一次登录统一使用 `aaaa0000*`，随后页面会强制改成至少 12 个字符的个人密码。`JX_ADMIN_NAMES` 必须是人员名单中准确且唯一的姓名，正式试用建议配置两名管理员。不要发送或提交 `.env`。程序会拒绝示例 `203.0.113.20`、内网地址、关闭个人账号模式、示例会话密钥和示例管理员；启动后还会拒绝同名启用人员。唯一管理员忘记密码时，按[完整部署说明](阿里云宝塔部署说明_V0.5.1.md)中的本机命令行方式应急重置。

保存后再次准备数据目录：

```bash
sudo bash deploy/cloud/scripts/prepare-host.sh --ip
```

## 7. 启动应用

```bash
cd /www/jx-handover/app
bash deploy/cloud/scripts/deploy.sh
```

成功后检查：

```bash
cd /www/jx-handover/app/deploy/cloud
docker compose ps
docker compose logs --tail 120 app
ss -lntp | grep 8765
```

此时 Docker 只能发布宿主机 `127.0.0.1:8765`；不能看到 `0.0.0.0:8765`。公网 1215 将在后续 Nginx 配置完成后出现。

本机健康检查需要正式 Host：

```bash
curl -H 'Host: 你的真实公网IPv4:1215' http://127.0.0.1:8765/api/health
```

应返回 `status: ok`、`version: 0.5.1`、`mode: cloud`、`login_mode: account`、内部 `port: 8765` 和 `public_port: 1215`。

## 8. 申请并安装公网 IP 证书

确认宝塔 IP 站点和 HTTP 80 正常后执行：

```bash
cd /www/jx-handover/app
sudo bash deploy/cloud/scripts/install-ip-certificate.sh 你的真实公网IPv4
```

脚本会：

1. 拒绝内网、回环和格式错误的地址；
2. 检查宝塔站点根目录、Nginx 和 `acme.sh`；
3. 使用 Let’s Encrypt `shortlived` 配置申请 IP 证书；
4. 安装到 `/www/server/panel/vhost/cert/公网IPv4`；
5. 保存 Nginx 自动重载命令；
6. 安装并检查 `acme.sh --cron` 自动续签任务；
7. 证书文件或续签任务缺失时返回失败，不把一次性证书声明为部署完成。

如果提示没有 `acme.sh`，先按宝塔官方 IP 证书教程安装或升级，完成后重新执行本脚本。

## 9. 自动配置宝塔 Nginx

先查出你当前用于访问宝塔电脑的公网出口 IPv4。该地址不是 ECS 公网 IP，也不是电脑的 `192.168.x.x` 内网地址。

然后执行：

```bash
cd /www/jx-handover/app
sudo bash deploy/cloud/scripts/configure-ip-nginx.sh \
  你的ECS真实公网IPv4 \
  允许访问系统的公网出口IPv4
```

脚本只接受两个精确 IPv4，并会：

- 备份宝塔原站点配置；
- 写入公网 HTTPS 1215、证书路径、ACME 续签例外、IP 白名单和内部 `127.0.0.1:8765` 反向代理；
- 写入固定的转发 Host、协议和端口，避免非标准 HTTPS 端口被重定向到默认 443；
- 修改前检查 1215 是否被非 Nginx 程序占用；
- 执行 Nginx 配置检查；
- 配置检查或重载失败时自动恢复原配置；
- 重载后确认 1215 确实由 Nginx 监听，否则恢复原配置并报错。

原配置备份文件名带 `.before-jx-日期时间.bak`，不会被 Nginx 的 `*.conf` 通配加载。

## 10. 浏览器验收

从白名单内电脑访问：

```text
https://ECS公网IPv4:1215
```

要求：

- 浏览器证书可信，不显示自签名警告；
- 能显示人员登录页；
- 账号输入本人准确姓名，初始密码为 `aaaa0000*`；首次登录必须先设置至少 12 个字符的个人密码；
- 改密后才能打开班次页面，初始密码不再可用；
- 每个人都使用自己的账号，不共享新密码；管理员可在系统管理页安全重置其他人员密码；
- 地址栏明确使用 `:1215`，并且不出现内部端口 `:8765`；
- 首次直接访问 `http://ECS公网IPv4` 时会跳转到 HTTPS 1215。

请把 `https://ECS公网IPv4:1215` 保存为浏览器收藏夹。由于 HSTS 会升级不带端口的 HTTP 地址，后续只输入公网 IP 可能让浏览器尝试默认 HTTPS 443；HTTP 跳转只能作为初次便利，不能代替显式的 `:1215`。

关闭手机 Wi-Fi，使用手机流量访问同一地址，必须返回 403。若手机流量也能看到登录页，说明白名单没有生效，禁止继续录入真实数据。

## 11. 自动续签验收

执行：

```bash
crontab -l | grep 'acme.sh.*--cron'
/root/.acme.sh/acme.sh --info -d 你的真实公网IPv4
```

必须看到每日续签任务和下一次续签时间。IP 证书约 6 天有效，因此要把“证书到期时间不足 48 小时”纳入服务器监控；只申请成功一次但没有自动续签，不算完成。

## 12. 后续换成域名

取得域名后：

1. DNS A 记录指向相同 ECS 公网 IPv4；
2. 宝塔创建域名站点并申请普通自动续签证书；
3. 修改 `.env` 的 `JX_PUBLIC_URL=https://新域名:1215` 和 `JX_TRUSTED_HOSTS`；
4. 重建/重启应用；
5. Nginx 仍在公网监听 1215，并反代内部 `127.0.0.1:8765`；
6. 数据库和 `/www/jx-handover/data` 不需要迁移。

内地 ECS 使用域名提供网站服务时，先按规定完成 ICP 备案。
