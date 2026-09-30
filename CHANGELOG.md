# Changelog

## 0.2.0

- English repository homepage with a full Simplified Chinese README.
- Agent connection page with official Cursor/VS Code install links, Codex/Claude Code commands and configuration downloads.
- MIME part reading: large attachments no longer prevent body access; bounded body pagination and attachment downloads.
- Replies, reply-all, forwarding and outgoing attachments with immutable previews.
- Durable, client-bound send status and partial-acceptance receipts.
- Batched mail headers, richer search filters and UID cursor pagination.
- CIMD registration, issuer-bound responses, native loopback callbacks and DCR fallback.
- Owner sessions, client revocation, IMAP checks and outgoing records.
- Private initialization, consistent backups and offline restore with key verification.
- Authentication endpoint limits and expanded integration coverage.


## 0.1.0 — 2026-09-30

- Initial public release of a single-mailbox QQ/Foxmail MCP server.
- Read-only listing, combined search, body reading, and attachment-name inspection.
- OAuth discovery, dynamic client registration, S256 PKCE, refresh rotation, and revocation.
- Encrypted SQLite mailbox credentials, authorization state, drafts, and send records.
- Configurable read-only, per-draft browser approval, and direct-send modes.
- Durable, client-scoped direct-send deduplication and no automatic resend on uncertain results.
- Credential rotation preserves grants for the same mailbox; mailbox replacement revokes grants.
- Docker Compose forwards the direct-send setting; Caddy preserves browser form submission.
- Chinese and English READMEs and deployment guides, MIT license, and GitHub CI.
