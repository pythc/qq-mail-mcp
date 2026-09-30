import base64
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from starlette.testclient import TestClient
from test_http import csrf

from qq_mail_mcp.connect import recipes
from qq_mail_mcp.maintenance import backup, initialize, restore
from qq_mail_mcp.store import Store


def test_official_install_links_decode_to_credential_free_configuration():
    values = recipes("https://mail.example.com")
    cursor = parse_qs(urlsplit(values["cursor"]["install_url"]).query)
    assert cursor["name"] == ["qq-mail"]
    assert json.loads(base64.b64decode(cursor["config"][0])) == {
        "url": "https://mail.example.com/mcp"
    }
    vscode = json.loads(unquote(urlsplit(values["vscode"]["install_url"]).query))
    assert vscode == {"name": "qq-mail", "type": "http", "url": "https://mail.example.com/mcp"}
    assert "codex mcp login qq-mail" in values["codex"]["command"]
    assert "--transport http --scope user" in values["claude-code"]["command"]
    assert values["windsurf"]["config"]["mcpServers"]["qq-mail"]["serverUrl"].endswith("/mcp")


@pytest.mark.parametrize(
    "url",
    [
        "https://u:p@mail.example.com",
        "http://mail.example.com",
        "https://mail.example.com/path",
        "https://mail.example.com/?token=secret",
    ],
)
def test_connection_urls_reject_credentials_and_unsupported_paths(url):
    with pytest.raises(ValueError):
        recipes(url)


def test_connect_page_downloads_and_languages(server, settings):
    with TestClient(server[3], base_url=settings.public_url) as client:
        page = client.get("/connect")
        assert (
            'lang="en"' in page.text
            and "cursor://" in page.text
            and "vscode:mcp/install" in page.text
        )
        assert "nonce=" in page.text
        assert "连接客户端" in client.get("/connect?lang=zh").text
        config = client.get("/connect/cursor")
        assert config.json() == {"mcpServers": {"qq-mail": {"url": settings.public_url + "/mcp"}}}
        assert "attachment;" in config.headers["content-disposition"]
        assert client.get("/connect/codex").text.startswith("[mcp_servers.qq-mail]")
        assert client.get("/connect/unknown").status_code == 404


def test_owner_login_session_csrf_revoke_and_logout(server, settings):
    _, store, oauth, app = server
    store.bind_mailbox("owner@qq.com", "secret-auth-code")
    token = oauth.issue("client-a", ["qq-mail.read"])
    store.put("grant", "client-a", {"client_id": "client-a", "client_name": "My agent"})
    with TestClient(app, base_url=settings.public_url) as client:
        assert "owner@qq.com" not in client.get("/").text
        login = client.get("/manage")
        assert "owner@qq.com" not in login.text
        result = client.post(
            "/manage", data={"csrf": csrf(login), "admin_password": settings.admin_password}
        )
        assert "owner@qq.com" in result.text and "My agent" in result.text
        assert "secret-auth-code" not in result.text and settings.admin_password not in result.text
        import re

        token_csrf = re.search(
            r'action="/manage/revoke".*?name="csrf" value="([^"]+)"', result.text, re.S
        )[1]
        assert (
            client.post(
                "/manage/revoke",
                data={"csrf": token_csrf, "client_id": "client-a"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert store.get("access", token.access_token)
        assert (
            client.post(
                "/manage/revoke", data={"csrf": token_csrf, "client_id": "client-a"}
            ).status_code
            == 200
        )
        assert store.get("access", token.access_token) is None
        assert store.get("refresh", token.refresh_token) is None
        result = client.get("/manage")
        logout_csrf = re.search(
            r'action="/manage/logout".*?name="csrf" value="([^"]+)"', result.text, re.S
        )[1]
        assert client.post("/manage/logout", data={"csrf": logout_csrf}).status_code == 200
        assert "owner@qq.com" not in client.get("/manage").text
        assert (
            client.post(
                "/manage/revoke", data={"csrf": token_csrf, "client_id": "client-a"}
            ).status_code
            == 403
        )


def test_init_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / ".env"
    initialize(path, "https://mail.example.com", direct=True)
    content = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "QQ_MCP_REQUIRE_SEND_APPROVAL=false" in content
    with pytest.raises(FileExistsError):
        initialize(path, "https://other.example.com")
    assert path.read_text() == content


def test_backup_restore_preserves_dedup_and_original_database(settings, tmp_path):
    store = Store(settings)
    store.bind_mailbox("owner@qq.com", "secret-code")
    store.direct_draft("client", "request", {"subject": "hi", "message_id": "<a@example.com>"})
    snapshot = tmp_path / "snapshot.sqlite3"
    backup(settings.database, snapshot)
    with pytest.raises(FileExistsError):
        backup(settings.database, snapshot)
    store.bind_mailbox("other@qq.com", "other-code")
    restore(settings, snapshot)
    restored = Store(settings)
    assert restored.get("mailbox", "owner")["email"] == "owner@qq.com"
    assert restored.send_status("client", request_id="request")["status"] == "pending"
    assert list(settings.database.parent.glob("*.before-restore-*"))
    assert b"secret-code" not in snapshot.read_bytes()


def test_legacy_connections_remain_visible_and_revocable(server):
    store, oauth = server[1:3]
    tokens = oauth.issue("legacy-client", ["qq-mail.read"])
    store.put("client", "legacy-client", {"client_name": "Legacy client"})
    assert store.grants()[0]["client_name"] == "Legacy client"
    store.revoke_client("legacy-client")
    assert store.grants() == []
    assert store.get("access", tokens.access_token) is None


def test_restore_wrong_key_preserves_live_database(settings, tmp_path):
    from dataclasses import replace

    from cryptography.fernet import Fernet, InvalidToken

    store = Store(settings)
    store.bind_mailbox("owner@qq.com", "secret-code")
    snapshot = tmp_path / "snapshot.sqlite3"
    backup(settings.database, snapshot)
    before = settings.database.read_bytes()
    with pytest.raises(InvalidToken):
        restore(replace(settings, encryption_key=Fernet.generate_key().decode()), snapshot)
    assert settings.database.read_bytes() == before
    assert store.get("mailbox", "owner")["email"] == "owner@qq.com"
