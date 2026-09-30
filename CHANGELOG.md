# Changelog

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
