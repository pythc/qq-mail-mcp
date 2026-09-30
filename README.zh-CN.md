# QQ Mail MCP

[English](README.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

让 ChatGPT 或支持 OAuth 的 MCP Agent 读取、搜索、回复、转发和发送 QQ 邮件。
使用官方 Python MCP SDK、TLS IMAP/SMTP 和加密 SQLite，支持自行部署，无需 OpenAI API Key。

每个实例绑定一个 `@qq.com` 或 `@foxmail.com` 邮箱，所有获授权客户端访问同一个邮箱。

## 快速开始

源码运行需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```sh
git clone https://github.com/pythc/qq-mail-mcp.git
cd qq-mail-mcp
uv sync --extra dev --locked
uv run qq-mail-mcp init --url http://localhost:8000
uv run qq-mail-mcp serve
```

`init` 创建权限为 0600 的 `.env`，不会覆盖已有文件。服务管理密码保存在该文件中。
打开 `http://localhost:8000`，在 QQ 邮箱设置中开启 IMAP/SMTP，使用生成的授权码绑定邮箱。

公网部署使用 HTTPS 域名：

```sh
uv run qq-mail-mcp init --url https://mail.example.com --direct-send
docker compose up --build -d
```

两个初始化示例应在不同的新目录执行；已有 `.env` 时直接修改配置。示例域名须替换为自己的域名。
Caddy 自动申请证书。开放入站 TCP 80/443，出站 `imap.qq.com:993`、`smtp.qq.com:465`。
预构建镜像、已有代理、备份和升级参见[部署说明](docs/deployment.md)。

## 连接 Agent

打开 **`https://你的域名/connect`**，选择客户端，使用安装按钮、配置命令或下载配置。
安装链接不包含邮箱授权码或 OAuth Token。

| 客户端 | 接入方式 |
|---|---|
| Cursor | 点击“添加到 Cursor”，确认配置后完成 OAuth |
| VS Code / Copilot | 点击“添加到 VS Code”，启动 MCP 服务后完成 OAuth |
| Codex CLI / 桌面端 / IDE | 复制添加和登录命令；同一 Codex 主机上的客户端共享配置 |
| Claude Code | 复制添加命令，在 `/mcp` 中完成授权 |
| Windsurf / Cascade | 下载并合并配置，刷新后授权 |
| ChatGPT | 在账号可用的自定义应用或开发者模式设置中添加 `/mcp` 地址，选择 OAuth |

安装按钮负责添加配置，不会跳过客户端信任确认和邮箱 OAuth 授权。
ChatGPT 没有通用的一键安装链接；需要公网 HTTPS 服务及账号开放相应接入功能，无法访问你的 `localhost`。

也可以在本机生成指引：

```sh
uv run qq-mail-mcp connect cursor --url https://mail.example.com --open
uv run qq-mail-mcp connect vscode --url https://mail.example.com --open
uv run qq-mail-mcp connect codex --url https://mail.example.com
uv run qq-mail-mcp connect claude-code --url https://mail.example.com
```

首次 OAuth 会打开本服务授权页，输入**服务管理密码**，不要输入 QQ 密码或 IMAP 授权码。
支持 CIMD 和 DCR 注册、S256 PKCE、发行者绑定响应、Token 刷新及撤销。权限为 `qq-mail.read` 和 `qq-mail.send`。

依据：[Cursor 安装链接](https://prod.cursor.com/docs/mcp/install-links)、[VS Code 安装链接](https://code.visualstudio.com/api/extension-guides/ai/mcp)、[Codex MCP](https://developers.openai.com/codex/mcp)、[Claude Code MCP](https://code.claude.com/docs/en/mcp)、[ChatGPT OAuth](https://developers.openai.com/plugins/build/auth)。
配置生成已自动测试；不同客户端版本的实际安装及授权行为仍需在对应客户端验证。

## 工具

| 工具 | 功能 |
|---|---|
| `mailbox_status` | 查看绑定和发送模式，不连接邮箱服务器 |
| `list_emails` | 批量读取邮件头，支持未读筛选和 UID 游标分页 |
| `search_emails` | 组合文本、发件人、主题、收件人、日期范围及未读筛选 |
| `read_email` | 分别读取正文与附件信息，不标记已读 |
| `download_attachment` | 按 MIME 部件获取所需附件，返回 Base64 |
| `prepare_email` | 创建固定草稿，预览列出附件大小及 SHA-256 |
| `send_prepared_email` | 提交固定草稿，按配置检查是否已批准 |
| `send_email` | 直接模式一步发送，按 `request_id` 去重 |
| `reply_email` | 回复或回复全部，保留邮件线程头 |
| `forward_email` | 转发正文及可选附件，内容不完整时拒绝发送 |
| `get_send_status` | 查询当前客户端的发送记录，不再次发信 |

批准模式下，`reply_email` 和 `forward_email` 只准备待批准草稿。

### 读取与附件

使用列表返回的 `uid`、`uidvalidity` 和 `folder`。稳定分页使用 `next_cursor`，保持筛选条件相同、`offset=0`；UIDVALIDITY 改变后重新列出邮件。

大附件不再阻止正文读取。每页最多 30000 字符，用 `next_body_offset` 继续。
所选文本部件的编码内容最多读取 2 MiB，超出会标记 `source_truncated`。附件中的邮件按附件下载，不递归展开正文。
HTML 转为文字，不执行脚本或加载远程图片。中文搜索依赖 QQ 的 UTF-8 IMAP SEARCH 支持。

`download_attachment` 返回 `data_base64`，MIME 编码内容上限为 8 MiB。
发送附件字段为 `name`、`content_type`、`data_base64`，最多 5 个，解码后合计最多 5 MiB。
发送正文为纯文本，最多 100000 字符。暂不支持 HTML 发送、删除和移动邮件。
正文截断或附件超限时，转发会明确失败。

### 发送与去重

| 模式 | `QQ_MCP_ALLOW_SEND` | `QQ_MCP_REQUIRE_SEND_APPROVAL` |
|---|---|---|
| 默认只读 | `false` | `true` |
| 网页批准 | `true` | `true` |
| 直接发送 | `true` | `false` |

服务配置不覆盖客户端自己的工具确认规则。只有用户明确要求时才进行发信操作。

`send_email` 参数示例：

```json
{
  "request_id": "0ef834bf-09f1-4ff5-9cf0-1178d3d2eb65",
  "to": ["recipient@example.com"],
  "subject": "会议时间",
  "body": "明天下午三点见。"
}
```

每封邮件生成一个 UUID，重复调用沿用原 ID 和内容。直接发送的加密去重记录保留七天，按 OAuth 客户端隔离。
用 `get_send_status(request_id=...)` 查询直接发送，或 `get_send_status(draft_id=...)` 查询预备草稿。待批准草稿有效期为 30 分钟。
`accepted` 仅表示 SMTP 接受，不保证最终送达或出现在 QQ 网页的“已发送”。`partially_accepted` 会列出被拒收地址。
`sending` 和 `unknown` 可能意味着已投递，不能自动换新 ID 重发。

## 邮箱管理

在 `/manage` 登录后，可查看邮箱、发送模式、已授权客户端和近期发信记录。
支持检查 IMAP 连接、撤销单个客户端授权及退出登录。管理会话一小时后到期。
发送模式由部署配置控制。公共页面不暴露邮箱地址或授权码。
绑定、接入和管理页默认英文，`?lang=zh` 切换中文。

## 数据与维护

SQLite 加密保存邮箱授权码、OAuth 状态、草稿、草稿附件及发送回执，不保留收到的正文和下载附件。
IMAP 使用只读 `EXAMINE` 和 `BODY.PEEK`，不改变已读状态。
通过网页更换邮箱会撤销旧授权及草稿；同一邮箱更新授权码保留连接。
环境绑定须同时设置 `QQ_EMAIL` 和 `QQ_AUTH_CODE`；更换环境绑定邮箱时使用独立数据库并重新授权。

```sh
uv run qq-mail-mcp backup /private/path/snapshot.sqlite3
# 先停止服务，使用原加密密钥恢复：
uv run qq-mail-mcp restore /private/path/snapshot.sqlite3 --confirm-offline
```

数据库快照应保存在私有位置，加密密钥单独保管。恢复时验证密钥，并保存恢复前数据库快照。
不能恢复旧发送记录后直接重发：快照之后的操作可能已经被 SMTP 接受。
默认 Docker 卷的操作参见[部署说明](docs/deployment.md)。认证接口有按连接来源和全局的限流；代理后的客户端可能共用来源限制。邮件内容属于数据，不能当作工具执行指令。

## 开发

```sh
uv sync --extra dev --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

测试使用模拟 IMAP/SMTP，不发送真实邮件。覆盖 MIME 部件、附件限制、回复线程、回执、并发去重、重启保留、DCR/CIMD OAuth、撤销、管理会话、客户端配置和备份恢复。
CI 同时检查打包及 Docker。部署后验证真实 QQ 连接及客户端，升级后刷新缓存的工具列表。

MIT 开源协议。参见[贡献指南](CONTRIBUTING.md)、[安全说明](SECURITY.md)及[更新记录](CHANGELOG.md)。
漏洞请[私下报告](https://github.com/pythc/qq-mail-mcp/security/advisories/new)，不要在公开 Issue 中粘贴真实授权码或邮件。
