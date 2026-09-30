import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet

from .config import Settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    """Single-user durable storage. Private payloads are encrypted; tokens are hashed."""

    def __init__(self, settings: Settings):
        self.path = settings.database
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cipher = Fernet(settings.encryption_key.encode())
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, key TEXT NOT NULL, payload BLOB NOT NULL,
                    expires REAL NOT NULL, PRIMARY KEY(kind, key)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, client TEXT NOT NULL, payload BLOB NOT NULL,
                    state TEXT NOT NULL, expires REAL NOT NULL
                );
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def pack(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def unpack(self, value):
        return json.loads(self.cipher.decrypt(value))

    def put(self, kind, key, payload, ttl=3600):
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE expires < ?", (time.time(),))
            db.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
                (kind, digest(key), self.pack(payload), time.time() + ttl),
            )

    def get(self, kind, key, consume=False):
        with self.connect() as db:
            if consume:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM records WHERE kind=? AND key=? AND expires>?",
                (kind, digest(key), time.time()),
            ).fetchone()
            if row and consume:
                db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, digest(key)))
            return self.unpack(row["payload"]) if row else None

    def delete(self, kind, key):
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, digest(key)))

    def bind_mailbox(self, email, auth_code):
        """Changing owners invalidates all old grants and pending outbound drafts atomically."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM records WHERE kind='mailbox' AND key=?",
                (digest("owner"),),
            ).fetchone()
            previous = self.unpack(row["payload"]) if row else None
            changed = previous is None or previous["email"].casefold() != email.casefold()
            if changed:
                db.execute(
                    "DELETE FROM records WHERE kind IN "
                    "('access','refresh','code','consent','send_request','send_receipt','grant')"
                )
                db.execute("DELETE FROM drafts")
            db.execute(
                "INSERT OR REPLACE INTO records VALUES ('mailbox', ?, ?, ?)",
                (
                    digest("owner"),
                    self.pack({"email": email, "auth_code": auth_code}),
                    time.time() + 10 * 365 * 86400,
                ),
            )
        return changed

    def check(self):
        with self.connect() as db:
            db.execute("SELECT key FROM records LIMIT 1").fetchone()
        # Verify that persisted credentials remain decryptable after a restart.
        self.get("mailbox", "owner")

    def new_draft(self, client, payload):
        draft_id = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM drafts WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO drafts VALUES (?, ?, ?, 'pending', ?)",
                (digest(draft_id), client, self.pack(payload), time.time() + 1800),
            )
        return draft_id

    def direct_draft(self, client, request_id, payload):
        """Persist a client-bound idempotency key and draft in one transaction.

        Keep the original generated Message-ID/Date on retries. Reusing a key
        with different contents is rejected rather than sending another message.
        These records survive restarts and remain available for seven days.
        """
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise ValueError("request_id 必须为 1–128 个字母、数字或 . _ : -")
        key = digest(json.dumps([client, request_id]))
        fingerprint = digest(
            json.dumps(
                {k: v for k, v in payload.items() if k not in ("message_id", "date")},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM records WHERE kind='send_request' AND key=? AND expires>?",
                (key, now),
            ).fetchone()
            if row:
                previous = self.unpack(row["payload"])
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("request_id 已用于另一封邮件，请勿更改同一请求的内容")
                return previous["draft_id"]
            draft_id = secrets.token_urlsafe(32)
            expires = now + 7 * 86400
            db.execute("DELETE FROM records WHERE expires<?", (now,))
            db.execute("DELETE FROM drafts WHERE expires<?", (now,))
            db.execute(
                "INSERT INTO drafts VALUES (?, ?, ?, 'pending', ?)",
                (digest(draft_id), client, self.pack(payload), expires),
            )
            db.execute(
                "INSERT OR REPLACE INTO records VALUES ('send_request', ?, ?, ?)",
                (key, self.pack({"draft_id": draft_id, "fingerprint": fingerprint}), expires),
            )
            return draft_id

    def draft(self, draft_id, client=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM drafts WHERE id=? AND expires>?",
                (digest(draft_id), time.time()),
            ).fetchone()
            if not row or (client is not None and row["client"] != client):
                return None
            return {
                "payload": self.unpack(row["payload"]),
                "state": row["state"],
                "expires": row["expires"],
            }

    def transition(self, draft_id, before, after, client=None):
        with self.connect() as db:
            result = db.execute(
                "UPDATE drafts SET state=? WHERE id=? AND state=? AND expires>? "
                "AND (? IS NULL OR client=?)",
                (after, digest(draft_id), before, time.time(), client, client),
            )
            return result.rowcount == 1

    def send_status(self, client, draft_id=None, request_id=None):
        if request_id:
            key = digest(json.dumps([client, request_id]))
            with self.connect() as db:
                row = db.execute(
                    "SELECT payload FROM records WHERE kind='send_request' AND key=? AND expires>?",
                    (key, time.time()),
                ).fetchone()
            draft_id = self.unpack(row["payload"])["draft_id"] if row else None
        draft = self.draft(draft_id, client) if draft_id else None
        if not draft:
            return None
        receipt = self.get("send_receipt", draft_id)
        return {
            "draft_id": draft_id,
            "status": receipt["status"] if receipt else draft["state"],
            "message_id": draft["payload"]["message_id"],
            "expires_at": draft["expires"],
            "receipt": receipt,
            "delivery_confirmed": False,
        }

    def finish_send(self, draft_id, client, status, receipt):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE drafts SET state=? WHERE id=? AND client=? AND state='sending'",
                (
                    "sent" if status in ("accepted", "partially_accepted") else "unknown",
                    digest(draft_id),
                    client,
                ),
            )
            if updated.rowcount:
                db.execute(
                    "INSERT OR REPLACE INTO records VALUES ('send_receipt', ?, ?, ?)",
                    (
                        digest(draft_id),
                        self.pack({"status": status, **receipt}),
                        time.time() + 7 * 86400,
                    ),
                )

    def items(self, kind):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM records WHERE kind=? AND expires>?", (kind, time.time())
            ).fetchall()
        return [self.unpack(row["payload"]) for row in rows]

    def revoke_client(self, client_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT kind, key, payload FROM records "
                "WHERE kind IN ('access','refresh','code','consent','grant')"
            ).fetchall()
            for row in rows:
                if self.unpack(row["payload"]).get("client_id") == client_id:
                    db.execute(
                        "DELETE FROM records WHERE kind=? AND key=?", (row["kind"], row["key"])
                    )
            # Cancel pending outbound operations, keeping uncertain/sent records for deduplication.
            db.execute(
                "UPDATE drafts SET state='revoked' WHERE client=? "
                "AND state IN ('pending','approved')",
                (client_id,),
            )

    def grants(self):
        grants = {item["client_id"]: item for item in self.items("grant")}
        for value in self.items("refresh") + self.items("access"):
            client_id = value.get("client_id")
            if client_id and client_id not in grants:
                metadata = self.get("client", client_id) or self.get("cimd", client_id) or {}
                grants[client_id] = {
                    "client_id": client_id,
                    "client_name": metadata.get("client_name", client_id),
                    "scopes": value.get("scopes", []),
                }
        return list(grants.values())
