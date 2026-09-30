#!/usr/bin/env python3
"""Consistent encrypted SQLite backup; no keys or plaintext payloads in this directory."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)
root = Path("/opt/qq-mail-mcp")
source = root / "data/qq-mail.sqlite3"
backups = root / "backups/database"
backups.mkdir(parents=True, exist_ok=True, mode=0o700)
name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3"
target = backups / name
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
# Retain the newest 30 daily snapshots. Never prune active state or private keys.
for expired in sorted(backups.glob("*.sqlite3"), reverse=True)[30:]:
    expired.unlink()
print("QQ Mail MCP encrypted database backup OK:", name)
