# Deployment and operations

[中文](deployment.md) · [English](deployment.en.md)

## Docker Compose

Prepare a domain, Docker, and Compose. Allow inbound 80/443 and outbound QQ IMAP 993 / SMTP 465. Point the domain at your host; Caddy obtains an HTTPS certificate.

```sh
cp .env.example .env
```

Generate settings with `uv run qq-mail-mcp generate-secrets`. For a Docker-only host, build and generate them in your own terminal:

```sh
docker build -t qq-mail-mcp:local .
docker run --rm qq-mail-mcp:local qq-mail-mcp generate-secrets
```

Set the admin password and Fernet key in `.env`, together with:

```dotenv
DOMAIN=mail.example.com
QQ_MCP_PUBLIC_URL=https://mail.example.com
QQ_MCP_ALLOW_SEND=true
QQ_MCP_REQUIRE_SEND_APPROVAL=false
```

Use `QQ_MCP_ALLOW_SEND=false` for read-only access, or `QQ_MCP_REQUIRE_SEND_APPROVAL=true` for per-message browser approval. Compose passes these settings into the application.

```sh
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

The mail port 8000 is container-network-only. The app runs as UID 10001 with a read-only root filesystem and a writable `/app/data` named volume. Caddy waits for app health. Bind your mailbox on the HTTPS home page, then connect a client to `/mcp` using OAuth / CIMD or DCR.

Do not dump expanded `docker compose config` or `docker inspect` environment values into shared logs: they may contain secrets.

## Acceptance checks

Replace the example domain:

```sh
curl --fail https://mail.example.com/healthz
curl --fail https://mail.example.com/.well-known/oauth-authorization-server
curl --fail https://mail.example.com/.well-known/oauth-protected-resource/mcp
curl -i -H 'Accept: application/json, text/event-stream' https://mail.example.com/mcp
```

Health should return `{"status":"ok"}`. Discovery should reference your origin and resource. Unauthenticated `/mcp` should return 401. Health verifies database access and stored-credential decryption, not live QQ connectivity. After OAuth, test binding status, listing, and body reading in that order. Use an explicitly chosen recipient and content for real sending tests.

## Configuration

| Variable | Meaning |
|---|---|
| `DOMAIN` | Public domain for Compose / Caddy |
| `QQ_MCP_PUBLIC_URL` | HTTPS origin without a path, query, fragment, or embedded credentials; HTTP loopback is allowed locally |
| `QQ_MCP_ADMIN_PASSWORD` | Random service admin password, at least 24 characters |
| `QQ_MCP_ENCRYPTION_KEY` | Valid Fernet key; back it up separately |
| `QQ_MCP_ALLOW_SEND` | Defaults to `false`; sending requires it to be enabled |
| `QQ_MCP_REQUIRE_SEND_APPROVAL` | Defaults to `true`; `false` enables direct sending |
| `QQ_MCP_DATABASE` | Defaults to `data/qq-mail.sqlite3`; Compose uses `/app/data/qq-mail.sqlite3` |
| `QQ_MCP_HOST` / `QQ_MCP_PORT` | Defaults to `127.0.0.1:8000`; Compose uses `0.0.0.0:8000` |
| `QQ_EMAIL` / `QQ_AUTH_CODE` | Optional environment binding; provide both. Browser rebinding is disabled in this mode |

Changing send mode does not change the database schema. Clients previously authorized only for reading still need send-scope authorization.

## Existing reverse proxy

You can replace Caddy with your own gateway. Preserve the original `Host`; the app validates Host and origins against its public origin. Subpath mounting such as `/mail` is unsupported. Do not allow arbitrary origins or remove OAuth verification.

Form pages require `Referrer-Policy: same-origin`. Overriding it with `no-referrer` can cause native browser forms to submit `Origin: null`, which is rejected. The app creates CSP `form-action` for validated OAuth callbacks; do not override it with an incompatible proxy CSP.

The base Caddy config provides TLS, security headers, and an 8 MiB request limit. The application limits auth traffic to 60 requests/minute per peer/class and 600/minute globally; clients behind a proxy may share a limit. Caddy itself does not rate-limit. Add gateway/platform rate limits and timeouts for `/register`, `/authorize`, `/token`, `/revoke`, home-page POST, `/consent/*`, and `/confirm/*`. The base image does not contain a third-party `rate_limit` directive.

Run one application process and persist SQLite. This project is not a public multi-tenant service or a horizontally scaled mail platform.

## Backup and recovery

The database contains encrypted credentials, OAuth state, and send records. Use SQLite's backup API for a consistent running-service snapshot, rather than copying an actively written database file.

Example for the Compose named volume:

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

Back up the encryption key separately. A same-host snapshot does not protect against losing the host. Optional `deploy/backup.py` and systemd examples assume a bind-mounted `/opt/qq-mail-mcp/data` directory and retain the latest 30 snapshots. Default Compose does not install these timers; adapt them to your storage layout first.

Before recovery, stop the app and preserve its current database. Use the original encryption key for the selected snapshot, restore UID 10001 and permissions 0600, then verify health, OAuth, and binding. Do not edit a live database directly.

## Upgrades, rollback, and revocation

1. Preserve the old source, image, and a current snapshot before rebuilding.
2. Recreate only the necessary services; keep data volumes. Refresh client tools if cached.
3. When reverting send mode, retain the latest database and use the old configuration. An outdated database can lose deduplication records.
4. A process exit during SMTP leaves `sending` state. Investigate delivery before retrying.
5. Stop with `docker compose stop`; revoking the authorization code in QQ Mail cuts mailbox access.

Browser credential rotation for the same mailbox preserves connections. First binding or browser mailbox replacement revokes old grants and drafts. Use a separate database and reauthorization when changing an environment-bound mailbox. On a shared host, change only this site's gateway configuration and preserve other sites.


## Prebuilt images and quick maintenance

```sh
# Published releases provide amd64 and arm64 images.
docker compose -f compose.yaml -f compose.prebuilt.yaml pull
docker compose -f compose.yaml -f compose.prebuilt.yaml up -d --no-build

# Create a snapshot in the default named volume and export it privately:
docker compose exec -T mail qq-mail-mcp backup /app/data/snapshot.sqlite3
mkdir -m 700 -p backups
docker compose cp mail:/app/data/snapshot.sqlite3 backups/snapshot.sqlite3
chmod 600 backups/snapshot.sqlite3
```

Existing snapshot paths are never overwritten; choose a fresh name each time. Keep the key separately. Stop the mail service before restoring, and preserve the latest send ledger to prevent duplicates.

```sh
docker compose stop mail
docker compose run --rm --no-deps -v "$PWD/backups:/restore:ro" mail qq-mail-mcp restore /restore/snapshot.sqlite3 --confirm-offline
docker compose up -d --no-build mail
```

`restore` verifies database integrity and the original encryption key, retaining a pre-restore snapshot. Do not use `--confirm-offline` while the service is running. Keep the database volume and encryption key across upgrades and rollbacks; this release uses compatible record kinds and does not remove old tables or fields.

Before rolling back to 0.1.x, disable sending and cancel pending/approved drafts: that version cannot interpret new attachment and reply-thread payloads. Preserve sent/unknown deduplication records; do not clear the database.
