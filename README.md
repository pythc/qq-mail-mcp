# QQ Mail MCP

[English](README.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Read, search, reply to, forward, and send QQ email from ChatGPT or an OAuth-capable MCP agent.
Self-hosted with the official Python MCP SDK, TLS IMAP/SMTP, and encrypted SQLite. No OpenAI API key is required.

Each instance binds one `@qq.com` or `@foxmail.com` mailbox. All authorized clients access that mailbox.

## Quick start

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required for running from source.

```sh
git clone https://github.com/pythc/qq-mail-mcp.git
cd qq-mail-mcp
uv sync --extra dev --locked
uv run qq-mail-mcp init --url http://localhost:8000
uv run qq-mail-mcp serve
```

`init` creates a private `.env` and refuses to overwrite an existing file. The service admin password is in that file.
Open `http://localhost:8000`, enable IMAP/SMTP in QQ Mail settings, and enter the generated QQ authorization code to bind the mailbox.

For a remote deployment, use an HTTPS domain:

```sh
uv run qq-mail-mcp init --url https://mail.example.com --direct-send
docker compose up --build -d
```

Run either initialization example in a fresh directory, or edit your existing `.env`. Replace the example domain with your own.
Caddy obtains the certificate; allow inbound TCP 80/443 and outbound `imap.qq.com:993` and `smtp.qq.com:465`.
See [Deployment](docs/deployment.en.md) for prebuilt images, existing proxies, backups and upgrades.

## Connect your agent

Open **`https://your-domain/connect`** for client-specific buttons, commands and configuration downloads.
No mailbox credentials or OAuth tokens are embedded in the installation links.

| Client | Connection |
|---|---|
| Cursor | **Add to Cursor** button → confirm configuration → authorize OAuth |
| VS Code / Copilot | **Add to VS Code** button → start the server → authorize OAuth |
| Codex CLI / desktop / IDE | Copy the add-and-login commands; these clients share Codex host configuration |
| Claude Code | Copy the add command, then authenticate through `/mcp` |
| Windsurf / Cascade | Download and merge the generated configuration, then refresh and authenticate |
| ChatGPT | Add the `/mcp` URL as an OAuth connection in your available custom-app / developer-mode settings |

The buttons install configuration; they do not bypass your client's trust or OAuth consent.
ChatGPT has no universal installation deeplink. A public HTTPS deployment and an account with the appropriate connection feature are needed; ChatGPT cannot reach your `localhost`.

You can also generate instructions locally:

```sh
uv run qq-mail-mcp connect cursor --url https://mail.example.com --open
uv run qq-mail-mcp connect vscode --url https://mail.example.com --open
uv run qq-mail-mcp connect codex --url https://mail.example.com
uv run qq-mail-mcp connect claude-code --url https://mail.example.com
```

First OAuth authorization opens this service's consent page. Enter the **service admin password**, not the QQ password or IMAP authorization code.
CIMD and DCR registration, S256 PKCE, issuer-bound responses, token refresh and revocation are supported. Scopes: `qq-mail.read`, `qq-mail.send`.

Sources: [Cursor install links](https://prod.cursor.com/docs/mcp/install-links), [VS Code install links](https://code.visualstudio.com/api/extension-guides/ai/mcp), [Codex MCP](https://developers.openai.com/codex/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [ChatGPT OAuth](https://developers.openai.com/plugins/build/auth).
Client interfaces change; configuration generation is tested, but installation and OAuth behavior must be checked with your installed client version.

## Tools

| Tool | Purpose |
|---|---|
| `mailbox_status` | Check binding and send mode without opening a mail connection |
| `list_emails` | Batched mail headers; unread filtering and UID cursor pagination |
| `search_emails` | Combine text, sender, subject, recipient, date-range and unread filters |
| `read_email` | Read text and attachment metadata independently, without marking read |
| `download_attachment` | Fetch a requested MIME attachment as Base64 |
| `prepare_email` | Create an immutable draft; preview lists attachment sizes and SHA-256 hashes |
| `send_prepared_email` | Submit the fixed draft, checking browser approval when enabled |
| `send_email` | One-step sending in direct mode, deduplicated by `request_id` |
| `reply_email` | Reply or reply-all, preserving `In-Reply-To` and `References` |
| `forward_email` | Forward text, optionally including attachments; incomplete content is rejected |
| `get_send_status` | Query your client's draft/request receipt without sending again |

In browser-approval mode, `reply_email` and `forward_email` prepare a draft for approval rather than sending it immediately.

### Reading and attachments

Pass the `uid`, `uidvalidity` and `folder` returned by a listing. For stable pagination, pass `next_cursor` with the same filters and `offset=0`; UIDVALIDITY changes require a new listing.

A large attachment no longer blocks body reading. Each body page returns up to 30,000 characters; use `next_body_offset` to continue.
The selected encoded text part is bounded to 2 MiB, indicated by `source_truncated`. Attached messages are downloadable attachments, not recursively expanded text.
HTML is converted to text without fetching remote images or executing scripts. Chinese search requires QQ's UTF-8 IMAP SEARCH support.

`download_attachment` returns `data_base64`; encoded MIME parts are limited to 8 MiB. Outgoing attachments use `name`, `content_type`, `data_base64`; at most 5 files, totaling 5 MiB decoded. Send bodies are plain text, up to 100,000 characters. HTML sending, deleting and moving mail are not supported.
Forwarding fails explicitly if the body is truncated or attachment limits are exceeded.

### Sending and deduplication

| Mode | `QQ_MCP_ALLOW_SEND` | `QQ_MCP_REQUIRE_SEND_APPROVAL` |
|---|---|---|
| Read-only, default | `false` | `true` |
| Browser approval | `true` | `true` |
| Direct sending | `true` | `false` |

Server settings do not override client-side tool confirmation. Outgoing actions must be requested by the user.

Example `send_email` arguments:

```json
{
  "request_id": "0ef834bf-09f1-4ff5-9cf0-1178d3d2eb65",
  "to": ["recipient@example.com"],
  "subject": "Meeting time",
  "body": "See you tomorrow at 3 PM."
}
```

Generate one UUID per intended email; retries reuse the original ID and contents. Encrypted direct-send records last seven days and are scoped to the OAuth client.
Query `get_send_status(request_id=...)` for direct sends or `get_send_status(draft_id=...)` for prepared drafts. Approval drafts expire after 30 minutes.
`accepted` means SMTP acceptance, not guaranteed delivery or an entry in QQ's Sent folder. `partially_accepted` lists refused recipients.
`sending` and `unknown` may mean delivery occurred: do not automatically retry with a new ID.

## Owner management

Open `/manage` and sign in to view the bound mailbox, send mode, authorized clients and recent outgoing records.
You can check IMAP connectivity, revoke a client's access, and sign out. Owner sessions expire after one hour.
Sending mode remains deployment-controlled. Public pages do not disclose the mailbox address or credentials.
English is the default for setup, connections and management; `?lang=zh` selects Chinese.

## Data and maintenance

SQLite encrypts mailbox credentials, OAuth state, drafts, attachments in outgoing drafts and send receipts. Received bodies and downloads are not retained.
Read-only `EXAMINE` and `BODY.PEEK` preserve read status. Changing a browser-bound mailbox revokes old grants and drafts; same-mailbox code rotation preserves them.
Environment binding requires both `QQ_EMAIL` and `QQ_AUTH_CODE`; changing it requires a separate database and fresh client authorization.

```sh
uv run qq-mail-mcp backup /private/path/snapshot.sqlite3
# Stop the service first; restore with the original encryption key:
uv run qq-mail-mcp restore /private/path/snapshot.sqlite3 --confirm-offline
```

Snapshots must be stored privately; keep the encryption key separately. Restore refuses mismatched keys and retains a snapshot of the previous database.
Never restore an older send ledger and resume sending blindly: operations after that snapshot might already have reached SMTP.
See [Deployment](docs/deployment.en.md) for default Docker named-volume procedures. Authentication routes have bounded per-peer and global traffic limits; behind a reverse proxy, peers may share a limit. Received mail is untrusted source data, never instructions to execute.

## Development

```sh
uv sync --extra dev --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Tests use simulated IMAP/SMTP and do not send real messages. They cover MIME part reading, attachment limits, threading, receipts, concurrent send deduplication, restart behavior, DCR/CIMD OAuth, revocation, owner sessions, connection recipes and backup/restore.
CI also validates packaging and Docker. Validate real QQ access and your client after deployment. Refresh cached tool lists after upgrades.

MIT License. See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md) and [Changelog](CHANGELOG.md).
Report vulnerabilities [privately](https://github.com/pythc/qq-mail-mcp/security/advisories/new); never include real credentials or messages in public issues.
