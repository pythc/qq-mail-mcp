import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet


@dataclass(frozen=True)
class Settings:
    public_url: str
    admin_password: str
    encryption_key: str
    database: Path
    allow_send: bool = False
    require_send_approval: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    email: str = ""
    auth_code: str = ""

    @classmethod
    def from_env(cls):
        settings = cls(
            public_url=os.getenv("QQ_MCP_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            admin_password=os.getenv("QQ_MCP_ADMIN_PASSWORD", ""),
            encryption_key=os.getenv("QQ_MCP_ENCRYPTION_KEY", ""),
            database=Path(os.getenv("QQ_MCP_DATABASE", "data/qq-mail.sqlite3")),
            allow_send=os.getenv("QQ_MCP_ALLOW_SEND", "false").lower() == "true",
            require_send_approval=(
                os.getenv("QQ_MCP_REQUIRE_SEND_APPROVAL", "true").lower() != "false"
            ),
            host=os.getenv("QQ_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("QQ_MCP_PORT", "8000")),
            email=os.getenv("QQ_EMAIL", ""),
            auth_code=os.getenv("QQ_AUTH_CODE", ""),
        )
        settings.validate()
        return settings

    def validate(self):
        url = urlsplit(self.public_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("QQ_MCP_PUBLIC_URL must be an origin without a path or credentials")
        if url.scheme != "https" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote deployments require HTTPS")
        if len(self.admin_password) < 24:
            raise ValueError(
                "Set QQ_MCP_ADMIN_PASSWORD to a random value of at least 24 characters"
            )
        try:
            Fernet(self.encryption_key.encode("ascii"))
        except Exception as exc:
            raise ValueError("Set a valid QQ_MCP_ENCRYPTION_KEY (generate-secrets)") from exc
        if bool(self.email) != bool(self.auth_code):
            raise ValueError("Set both QQ_EMAIL and QQ_AUTH_CODE, or neither")
