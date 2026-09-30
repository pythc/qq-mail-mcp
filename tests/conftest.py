import secrets
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from qq_mail_mcp.config import Settings
from qq_mail_mcp.server import AuthGuard, BodyLimit, RevocationFormCompat, build_server


@pytest.fixture
def settings(tmp_path):
    return Settings(
        public_url="http://localhost:8000",
        admin_password=secrets.token_urlsafe(32),
        encryption_key=Fernet.generate_key().decode(),
        database=tmp_path / "state" / "test.sqlite3",
        allow_send=True,
    )


@pytest.fixture
def server(settings):
    mcp, store, oauth = build_server(settings)
    return (
        mcp,
        store,
        oauth,
        BodyLimit(AuthGuard(RevocationFormCompat(mcp.streamable_http_app()), settings)),
    )


@pytest.fixture
def direct_settings(settings):
    return replace(settings, require_send_approval=False)


@pytest.fixture
def direct_server(direct_settings):
    mcp, store, oauth = build_server(direct_settings)
    return (
        mcp,
        store,
        oauth,
        BodyLimit(AuthGuard(RevocationFormCompat(mcp.streamable_http_app()), direct_settings)),
    )
