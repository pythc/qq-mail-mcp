# QQ Mail MCP

[中文](README.md) · [English](README.en.md)

[![CI](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pythc/qq-mail-mcp/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Read, search, and send personal QQ email from ChatGPT or another client that supports remote MCP and OAuth.
Self-hosted, built with the official Python MCP SDK, IMAP/SMTP over TLS, and encrypted SQLite. No OpenAI API key is required.

Each instance binds one mailbox, shared by every authorized client of that instance. Supports `@qq.com` and `@foxmail.com`.
Publishing this source on GitHub does not publish it to the ChatGPT public plugin directory.

## Features

| Tool | Purpose |
|---|---|
| `mailbox_status` | Check the mailbox binding, sending switch, and send mode |
| `list_emails` | Paginated mail listing, optional unread filter, up to 50 messages per page |
| `search_emails` | Combine text, sender, date, and unread filters |
| `read_email` | Read the body and attachment names without changing read status |
| `prepare_email` | Create an immutable recipient/content draft that expires after 30 minutes |
| `send_prepared_email` | Submit the same draft, with browser approval when configured |
| `send_email` | Send in one step in direct mode, deduplicated by `request_id` |

Sending supports plain text, CC, and BCC. Attachment sending, HTML sending, attachment downloads, deletion, and moving messages are not supported.
Bodies are limited to 30,000 returned characters; reading an entire message, including attachments, is limited to 2 MiB. Chinese search depends on the QQ IMAP server's UTF-8 SEARCH support.

## Three send modes

| Mode | `QQ_MCP_ALLOW_SEND` | `QQ_MCP_REQUIRE_SEND_APPROVAL` | Behavior |
|---|---|---|---|
| Read-only, default | `false` | `true` | Sending is disabled |
| Browser approval | `true` | `true` | Prepare → review and approve in the browser → submit |
| Direct sending | `true` | `false` | Call `send_email` after OAuth authorization, without per-message browser approval |

Sending still requires the `qq-mail.send` scope. Server settings do not override the client's own write-action confirmation rules.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/pythc/qq-mail-mcp.git
cd qq-mail-mcp
uv sync --extra dev --locked
cp .env.example .env
uv run qq-mail-mcp generate-secrets
```

Copy the generated `QQ_MCP_ADMIN_PASSWORD` and `QQ_MCP_ENCRYPTION_KEY` lines into your local `.env`, then start:

```sh
uv run qq-mail-mcp serve
```

Open `http://localhost:8000`. Enable IMAP/SMTP and generate an authorization code in QQ Mail settings. Enter the mailbox address, authorization code, and this service's admin password on the home page. The service verifies IMAP access and encrypts the credentials.

The service admin password, QQ account password, and QQ Mail authorization code are different credentials. Do not paste them into chats, public issues, or Git.

## Docker and HTTPS

Prepare a domain, Docker Compose, and a publicly reachable host. Allow inbound TCP 80/443 and outbound `imap.qq.com:993` and `smtp.qq.com:465`.

Set the admin password and encryption key in `.env`, along with:

```dotenv
DOMAIN=mail.example.com
QQ_MCP_PUBLIC_URL=https://mail.example.com
# Enable these two settings for direct sending:
QQ_MCP_ALLOW_SEND=true
QQ_MCP_REQUIRE_SEND_APPROVAL=false
```

Replace the placeholder domain with your own. For remote deployments, `QQ_MCP_PUBLIC_URL` must be an HTTPS origin without a subpath.

```sh
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl --fail https://mail.example.com/healthz
```

Caddy obtains HTTPS certificates automatically. The mail process runs as a non-root user, and its port is available only within the container network.
SQLite persists in the `mail_data` named volume. `docker compose down` keeps data; `down -v` deletes it.

See the [deployment guide](docs/deployment.en.md) for configuration, an existing reverse proxy, backups, and rollback. The base Caddy image has no third-party rate-limit module; add authentication-endpoint limits at your gateway or hosting platform.

## Connect ChatGPT

1. Deploy the HTTPS service and bind your mailbox on its home page. ChatGPT cannot reach your `localhost`.
2. Add `https://mail.example.com/mcp` through ChatGPT's developer-mode/plugin connection interface.
3. Select OAuth. This server uses dynamic client registration (DCR); do not enter the QQ authorization code in ChatGPT. Select DCR if a registration-method choice is offered.
4. Review the requested permissions on the service's authorization page and approve using the **service admin password**.
5. Enable the connection in a conversation, call `mailbox_status`, and try “List my five most recent unread emails.”

UI locations depend on the version and workspace policy. Refer to the [official OpenAI connection guide](https://developers.openai.com/plugins/quickstart) and [OAuth documentation](https://developers.openai.com/plugins/build/auth). Running this server does not grant developer-mode access to your account.

Scopes are `qq-mail.read` and `qq-mail.send`. The server supports discovery, DCR, authorization code + S256 PKCE, token refresh, and revocation; CIMD registration is not implemented. If you previously authorized only reading, authorize sending before using send tools.

## Tool usage

Pass the `uid`, `uidvalidity`, and `folder` returned by the mail listing when reading a body. Do not guess a UID.

Example direct-send arguments (an illustration, not an automatic send):

```json
{
  "request_id": "0ef834bf-09f1-4ff5-9cf0-1178d3d2eb65",
  "to": ["recipient@example.com"],
  "subject": "Meeting time",
  "body": "See you tomorrow at 3 PM."
}
```

Replace the example address and generate a new UUID for each new intended email. Repeated calls must reuse the original ID and exactly the same contents. Encrypted deduplication records are retained for seven days and are scoped to the OAuth client.

In browser-approval mode, review the immutable draft at the `approval_url` returned by `prepare_email`, then call `send_prepared_email(draft_id)` after approving it.

`accepted` means QQ SMTP accepted the message, not guaranteed delivery, reading, or an entry in the webmail Sent folder.
`sending` / `unknown` may mean the message was delivered. Never automatically resend it with a new `request_id`; investigate partial refusals before acting as well.

## Data and permissions

- SQLite encrypts mailbox credentials, OAuth state, drafts, and direct-send records. Received email bodies are not stored.
- Read-only IMAP `EXAMINE` and `BODY.PEEK` preserve read status.
- First browser binding or changing the mailbox revokes old client grants and drafts. Rotating the authorization code for the same mailbox preserves connections.
- Environment binding requires both `QQ_EMAIL` and `QQ_AUTH_CODE`. Use a separate database and reauthorize clients when changing an environment-bound mailbox.
- Mail content is processed by the connected client. Treat subjects, bodies, and names as source data, never as instructions to execute.
- Database recovery requires the original encryption key. Back up the database and key separately; keep real data and credentials out of images.

See [SECURITY.md](SECURITY.md) for additional details (Chinese).

## Development and validation

```sh
uv sync --extra dev --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Automated tests use mocked IMAP/SMTP and do not send real email. CI covers Python 3.12/3.13, linting, formatting, tests, package builds, Docker builds, and container authorization checks.
Real QQ sending/receiving and ChatGPT OAuth connection were validated during development; validate your own network and client after deployment.

Refresh the client's tool list after upgrades if it caches tools. Preserve the latest database and send records during rollback to retain deduplication state.

## Contributing and license

MIT License; see [LICENSE](LICENSE). Submit improvements through issues or pull requests; see [CONTRIBUTING.md](CONTRIBUTING.md) (Chinese).
Report vulnerabilities through [private vulnerability reporting](https://github.com/pythc/qq-mail-mcp/security/advisories/new), without exposing credentials or real email.
See [CHANGELOG.md](CHANGELOG.md) for version history.
