# 部署与运维

[中文](deployment.md) · [English](deployment.en.md)

## Docker Compose

准备域名、Docker 和 Compose，开放入站 80/443、出站 QQ IMAP 993 / SMTP 465。域名解析到主机后，Caddy 申请 HTTPS 证书。

```sh
cp .env.example .env
```

有 Python / uv 时通过 `uv run qq-mail-mcp generate-secrets` 生成配置；只有 Docker 时可以先构建镜像，在自己的终端生成：

```sh
docker build -t qq-mail-mcp:local .
docker run --rm qq-mail-mcp:local qq-mail-mcp generate-secrets
```

填写 `.env` 中的管理密码和 Fernet 密钥，并设置：

```dotenv
DOMAIN=mail.example.com
QQ_MCP_PUBLIC_URL=https://mail.example.com
QQ_MCP_ALLOW_SEND=true
QQ_MCP_REQUIRE_SEND_APPROVAL=false
```

如需只读，设置 `QQ_MCP_ALLOW_SEND=false`；如需逐封网页批准，设置 `QQ_MCP_REQUIRE_SEND_APPROVAL=true`。实际值会传入应用容器。

```sh
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

`mail` 仅在容器网络暴露 8000，以 UID 10001 运行，根文件系统只读；`/app/data` 为可写命名卷。Caddy 只在邮件服务健康后启动。浏览器访问自己的 HTTPS 首页绑定邮箱，客户端连接 `/mcp`，选择 OAuth / DCR。

不要将完整 `docker compose config`、`docker inspect` 环境变量输出到共享日志，它们可能展开秘密。

## 验收

使用自己的域名替换下面的示例：

```sh
curl --fail https://mail.example.com/healthz
curl --fail https://mail.example.com/.well-known/oauth-authorization-server
curl --fail https://mail.example.com/.well-known/oauth-protected-resource/mcp
curl -i -H 'Accept: application/json, text/event-stream' https://mail.example.com/mcp
```

健康接口应返回 `{"status":"ok"}`，发现接口应返回相同域名的资源与授权端点，未授权 `/mcp` 应返回 401。健康检查验证数据库与持久化邮箱凭据解密，不验证当时 QQ 邮箱网络是否畅通。完成 OAuth 后依次验证绑定、列表和正文读取；真实发信使用本人明确指定的收件人和内容。

## 配置

| 变量 | 说明 |
|---|---|
| `DOMAIN` | Compose / Caddy 的公开域名 |
| `QQ_MCP_PUBLIC_URL` | HTTPS origin，无路径、查询、片段或内嵌凭据；本地允许 HTTP loopback |
| `QQ_MCP_ADMIN_PASSWORD` | 随机服务管理密码，至少 24 字符 |
| `QQ_MCP_ENCRYPTION_KEY` | 有效 Fernet 密钥，需独立备份 |
| `QQ_MCP_ALLOW_SEND` | 默认 `false`，打开后才允许发送 |
| `QQ_MCP_REQUIRE_SEND_APPROVAL` | 默认 `true`，设为 `false` 开启直接发送 |
| `QQ_MCP_DATABASE` | 直接运行默认 `data/qq-mail.sqlite3`；Compose 固定 `/app/data/qq-mail.sqlite3` |
| `QQ_MCP_HOST` / `QQ_MCP_PORT` | 直接运行默认 `127.0.0.1:8000`；Compose 固定 `0.0.0.0:8000` |
| `QQ_EMAIL` / `QQ_AUTH_CODE` | 可选环境绑定，必须同时设置；此方式不允许网页改绑 |

发信模式不改变数据库结构。客户端首次只获取读取权限时，打开发送开关仍需重新授权发送权限。

## 已有反向代理

可以使用自己的网关替换 Caddy。保留原始 `Host`，应用根据公开 origin 校验 Host 与来源；不支持挂载在 `/mail` 等子路径。不要放行任意 Origin，也不要移除 OAuth 验证。

表单页面使用 `Referrer-Policy: same-origin`。网关不能改成 `no-referrer`，否则部分浏览器的原生表单可能产生 `Origin: null` 并被来源校验拒绝。应用会为已验证的 OAuth 回调生成 CSP `form-action`；不要在代理中用另一条 CSP 破坏回调。

基础 Caddy 配置含 TLS、安全响应头和 256 KiB 请求限制，**不含限流**。在网关 / 托管平台为 `/register`、`/authorize`、`/token`、`/revoke`、首页 POST、`/consent/*`、`/confirm/*` 增加限流和超时。基础镜像没有第三方 `rate_limit` 指令，不能直接加入未安装的模块。

应用进程应只有一个。SQLite 需要持久化；本项目不面向公共多租户或多实例横向扩展。

## 备份与恢复

数据库含加密凭据、OAuth 状态和发送记录。运行中使用 SQLite backup API 获取一致性快照，不要仅复制正在写入的数据库。

为 Compose 命名卷创建快照的示例：

```sh
docker compose exec -T mail python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
os.umask(0o077)
root = Path('/app/data/backups')
root.mkdir(mode=0o700, exist_ok=True)
target = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
with sqlite3.connect('file:/app/data/qq-mail.sqlite3?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print(target.name)
PY
docker compose cp mail:/app/data/backups ./backups
```

另行备份加密密钥；快照保留在同一主机不能抵御整机丢失。可选的 `deploy/backup.py` 与 systemd 文件假定绑定目录为 `/opt/qq-mail-mcp/data`，并保留最新 30 份快照；默认 Compose 命名卷不会自动安装这些定时任务，使用前按实际存储布局调整。

恢复前停服务、保留当前数据库快照，使用目标快照对应的原加密密钥，恢复文件为 UID 10001、权限 0600；启动后检查 `/healthz`、OAuth 与绑定状态。不要在运行中直接编辑数据库。

## 升级、回滚与撤销

1. 保留旧源码、镜像和当前数据快照，再构建新镜像。
2. 仅重建应用与必要代理，不删除数据卷。客户端若缓存工具，刷新工具列表。
3. 回滚发送模式时使用旧配置并保留最新数据库；不要恢复过时发送状态，否则可能失去防重记录。
4. SMTP 过程中进程退出会保留 `sending`，先核查实际投递，不自动重发。
5. 立即停止服务可用 `docker compose stop`；QQ 邮箱中撤销授权码可切断邮箱访问。

同邮箱网页更新授权码保留客户端连接；首次绑定或网页更换邮箱撤销旧客户端授权及草稿。环境绑定的邮箱改变时，用独立数据库重新授权。共享服务器上的网关修改应仅调整此服务的站点配置，保留其他站点。
