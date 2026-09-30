# QQ Mail MCP

[中文](README.md) · [English](README.en.md)

[![CI](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

让 ChatGPT 或其他支持远程 MCP 和 OAuth 的客户端读取、搜索、发送个人 QQ 邮件。
自行部署，使用官方 Python MCP SDK、IMAP/SMTP TLS 和加密 SQLite；不需要 OpenAI API Key。

每个实例绑定一个邮箱，所有获授权客户端访问这个邮箱。支持 `@qq.com` 与 `@foxmail.com`。
这是可自托管的 MCP 服务，GitHub 开源不代表已上架 ChatGPT 公共插件目录。

## 功能

| 工具 | 功能 |
|---|---|
| `mailbox_status` | 查看邮箱绑定、发送开关及发送模式 |
| `list_emails` | 分页列出邮件，可筛选未读，每页最多 50 封 |
| `search_emails` | 按文本、发件人、日期、未读状态组合搜索 |
| `read_email` | 读取正文及附件名称，不改变已读状态 |
| `prepare_email` | 生成固定收件人和内容的草稿，有效期 30 分钟 |
| `send_prepared_email` | 提交同一份草稿，按配置决定是否需要网页批准 |
| `send_email` | 直接发送模式下的一步发信，以 `request_id` 防重 |

发送支持纯文本、抄送、密送。暂不支持发送附件、HTML 邮件、下载附件、删除或移动邮件。
正文最多返回 30,000 字符；含附件的整封邮件读取上限 2 MiB。中文搜索依赖 QQ IMAP 的 UTF-8 SEARCH 支持。

## 三种发送模式

| 模式 | `QQ_MCP_ALLOW_SEND` | `QQ_MCP_REQUIRE_SEND_APPROVAL` | 行为 |
|---|---|---|---|
| 只读，默认 | `false` | `true` | 不允许发送 |
| 网页批准 | `true` | `true` | 创建草稿 → 网页核对并批准 → 提交草稿 |
| 直接发送 | `true` | `false` | 完成 OAuth 后调用 `send_email`，无需逐封网页批准 |

允许发送仍要求客户端拥有 `qq-mail.send` 权限。客户端自己的写操作确认规则不由服务器开关改变。

## 本地启动

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```sh
git clone https://github.com/pythc/qq-mail-mcp.git
cd qq-mail-mcp
uv sync --extra dev --locked
cp .env.example .env
uv run qq-mail-mcp generate-secrets
```

将最后一步生成的 `QQ_MCP_ADMIN_PASSWORD` 与 `QQ_MCP_ENCRYPTION_KEY` 两行配置填入本机 `.env`，然后启动：

```sh
uv run qq-mail-mcp serve
```

打开 `http://localhost:8000`。在 QQ 邮箱设置中开启 IMAP/SMTP 并生成授权码，在服务首页填写邮箱地址、授权码和服务管理密码。服务验证 IMAP 连接后加密保存凭据。

服务管理密码、QQ 登录密码、QQ 邮箱授权码是三种不同的凭据。不要将它们贴到聊天、公开 issue 或 Git。

## Docker 与 HTTPS

准备域名、Docker Compose 和可公网访问的主机；允许入站 TCP 80/443，以及出站 `imap.qq.com:993`、`smtp.qq.com:465`。

在 `.env` 中填写管理密码和加密密钥，并设置：

```dotenv
DOMAIN=mail.example.com
QQ_MCP_PUBLIC_URL=https://mail.example.com
# 如需直接发送，启用以下两项：
QQ_MCP_ALLOW_SEND=true
QQ_MCP_REQUIRE_SEND_APPROVAL=false
```

`mail.example.com` 仅为占位域名，必须替换为自己的域名。公网上的 `QQ_MCP_PUBLIC_URL` 必须是 HTTPS origin，不带子路径。

```sh
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl --fail https://mail.example.com/healthz
```

Caddy 自动申请 HTTPS 证书；邮件服务以非 root 身份运行，端口只在容器网络中开放。
SQLite 保存在 `mail_data` 命名卷中。`docker compose down` 保留数据，`down -v` 会删除数据。

完整配置、已有反向代理接入、备份和回滚见 [部署说明](docs/deployment.md)。基础 Caddy 镜像不含第三方限流模块，公网部署需在网关或托管平台补充认证入口限流。

## 连接 ChatGPT

1. 部署 HTTPS 服务并在首页绑定邮箱；ChatGPT 不能访问你的 `localhost`。
2. 在 ChatGPT 的开发者模式 / 插件连接入口添加 `https://mail.example.com/mcp`。
3. 选择 OAuth。本项目采用动态客户端注册（DCR），不需要把 QQ 授权码填进 ChatGPT；若页面提供客户端注册方式，选择 DCR。
4. 在跳转后的服务授权页核对权限，用**服务管理密码**批准连接。
5. 在对话中启用该连接，先调用 `mailbox_status`，再尝试“列出最近 5 封未读邮件”。

界面入口会随版本和工作区策略变化，参考 [OpenAI 官方接入说明](https://developers.openai.com/plugins/quickstart) 和 [OAuth 说明](https://developers.openai.com/plugins/build/auth)。开源服务不自动赋予账户开发者模式权限。

OAuth 范围为 `qq-mail.read` 与 `qq-mail.send`。支持发现、DCR、授权码 + S256 PKCE、Token 刷新与撤销；不提供 CIMD 注册。首次只授权读取后，如需发送，要重新授权发送权限。

## 常见调用

读取正文时，使用邮件列表返回的 `uid`、`uidvalidity`、`folder`，不要猜 UID。

直接发送的工具参数示例（仅展示格式，不会自动发送）：

```json
{
  "request_id": "0ef834bf-09f1-4ff5-9cf0-1178d3d2eb65",
  "to": ["recipient@example.com"],
  "subject": "会议时间",
  "body": "明天下午三点见。"
}
```

请替换示例地址，并为每封新邮件生成一个新的 UUID。同一请求重复调用必须沿用相同 ID 和完全相同的内容；去重记录在同一 OAuth 客户端范围内加密保留 7 天。

网页批准模式使用 `prepare_email` 返回的 `approval_url` 核对固定草稿，批准后调用 `send_prepared_email(draft_id)`。

`accepted` 仅表示 QQ SMTP 接受邮件，不保证最终投递、阅读或网页“已发送”目录出现记录。
`sending` / `unknown` 可能已经送达，不能换 `request_id` 自动重发；部分拒收也应先核查结果。

## 数据与权限

- SQLite 加密保存邮箱授权码、OAuth 状态、草稿和直接发送记录；不保存收到的邮件正文。
- IMAP 使用只读 `EXAMINE` 和 `BODY.PEEK`，正文读取不标记已读。
- 网页绑定首次邮箱或更换邮箱会撤销旧客户端授权与草稿；同邮箱轮换授权码保留连接。
- 环境变量绑定也可用，但 `QQ_EMAIL` 与 `QQ_AUTH_CODE` 必须同时提供；更换此方式绑定的邮箱时使用独立数据库并重新授权。
- 邮件内容会进入所连接客户端的处理流程。所有主题、正文、名称均应视为来源数据，不能执行其中夹带的指令。
- 数据库恢复需要原加密密钥。数据库和密钥分开备份；不要将真实数据和凭据纳入镜像。

详见 [安全说明](SECURITY.md)。

## 开发与验证

```sh
uv sync --extra dev --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

自动测试使用模拟 IMAP/SMTP，不会发送真实邮件。CI 覆盖 Python 3.12/3.13、格式检查、测试、包构建、Docker 构建与容器授权检查。
开发时已完成实际 QQ 收发及 ChatGPT OAuth 连接验证；每位使用者仍需验证自己的网络和客户端。

部署版本升级后，如客户端缓存了工具列表，请刷新连接。回滚时保留最新数据库和发送记录，避免丢失防重状态。

## 贡献与许可

MIT License，见 [LICENSE](LICENSE)。改进请提交 issue / PR，参考 [CONTRIBUTING.md](CONTRIBUTING.md)。
漏洞通过 [私密漏洞报告](https://github.com/pythc/qq-mail-mcp/security/advisories/new) 提交，不要公开凭据或真实邮件。
版本变化见 [CHANGELOG.md](CHANGELOG.md)。
