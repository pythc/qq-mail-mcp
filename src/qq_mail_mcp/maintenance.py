"""Explicit, local-only initialization and SQLite maintenance."""

import os
import secrets
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet

from .connect import endpoint


def initialize(path, public_url, direct=False):
    url = endpoint(public_url).removesuffix("/mcp")
    from urllib.parse import urlsplit

    domain = urlsplit(url).netloc
    path = Path(path)
    content = (
        f"DOMAIN={domain}\nQQ_MCP_PUBLIC_URL={url}\n"
        f"QQ_MCP_ADMIN_PASSWORD={secrets.token_urlsafe(32)}\n"
        f"QQ_MCP_ENCRYPTION_KEY={Fernet.generate_key().decode()}\n"
        "QQ_MCP_DATABASE=data/qq-mail.sqlite3\n"
        f"QQ_MCP_ALLOW_SEND={'true' if direct else 'false'}\n"
        f"QQ_MCP_REQUIRE_SEND_APPROVAL={'false' if direct else 'true'}\n"
    )
    # Exclusive creation preserves existing keys and follows no existing symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    return path


def backup(database, target):
    database, target = Path(database), Path(target)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(target) as dest:
                source.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Database integrity check failed")
    except BaseException:
        target.unlink()
        raise
    return target


def restore(settings, source):
    source = Path(source)
    database = settings.database
    if source.resolve() == database.resolve():
        raise ValueError("Restore source cannot be the active database")
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Stop the service and clear SQLite transactions before restore")
    cipher = Fernet(settings.encryption_key.encode())
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Restore source integrity check failed")
        for table in ("records", "drafts"):
            for (payload,) in db.execute(f"SELECT payload FROM {table}"):
                cipher.decrypt(payload)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = database.with_name(database.name + ".restore-" + secrets.token_hex(8))
    backup(source, temporary)
    try:
        if database.exists():
            backup(
                database,
                database.with_name(database.name + ".before-restore-" + secrets.token_hex(8)),
            )
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)
