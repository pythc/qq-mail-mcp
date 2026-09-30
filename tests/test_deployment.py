from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from qq_mail_mcp.store import Store


def test_switching_mailbox_revokes_grants_and_drafts(settings):
    store = Store(settings)
    store.bind_mailbox("first@qq.com", "old-code")
    store.put("client", "client", {"name": "Test client"})
    for kind in ("access", "refresh", "code", "consent"):
        store.put(kind, "credential", {"client_id": "client"})
    draft = store.new_draft("client", {"from": "first@qq.com"})
    assert store.bind_mailbox("second@qq.com", "new-code") is True
    for kind in ("access", "refresh", "code", "consent"):
        assert store.get(kind, "credential") is None
    assert store.draft(draft) is None
    assert store.get("client", "client") is not None
    assert Store(settings).get("mailbox", "owner")["email"] == "second@qq.com"


def test_same_mailbox_credential_refresh_preserves_grants(settings):
    store = Store(settings)
    store.bind_mailbox("owner@qq.com", "old-code")
    store.put("access", "credential", {"client_id": "client"})
    assert store.bind_mailbox("owner@qq.com", "new-code") is False
    assert store.get("access", "credential") is not None
    assert store.get("mailbox", "owner")["auth_code"] == "new-code"


def test_first_binding_requires_existing_clients_to_reauthorize(settings):
    store = Store(settings)
    store.put("access", "pre-setup-token", {"client_id": "client"})
    store.bind_mailbox("owner@qq.com", "code")
    assert store.get("access", "pre-setup-token") is None


def test_health_checks_database_and_key_without_revealing_secrets(server, settings):
    _, store, _, app = server
    store.bind_mailbox("owner@qq.com", "private-auth-code")
    with TestClient(app, base_url=settings.public_url) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        store.cipher = Fernet(Fernet.generate_key())
        response = client.get("/healthz")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert "private-auth-code" not in response.text


def test_public_client_can_revoke_without_client_secret(server, settings):
    _, _, oauth, app = server
    with TestClient(app, base_url=settings.public_url) as client:
        registered = client.post(
            "/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "token_endpoint_auth_method": "none",
            },
        ).json()
        client_id = registered["client_id"]
        tokens = oauth.issue(client_id, ["qq-mail.read"])
        revoked = client.post(
            "/revoke",
            data={
                "client_id": client_id,
                "token": tokens.access_token,
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert oauth.store.get("access", tokens.access_token) is None
        assert oauth.store.get("refresh", tokens.refresh_token) is None


def test_revocation_compat_does_not_bypass_confidential_client_secret(server, settings):
    _, _, oauth, app = server
    with TestClient(app, base_url=settings.public_url) as client:
        registered = client.post(
            "/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        ).json()
        client_id = registered["client_id"]
        tokens = oauth.issue(client_id, ["qq-mail.read"])
        form = {"client_id": client_id, "token": tokens.access_token}
        assert client.post("/revoke", data=form).status_code == 401
        assert oauth.store.get("access", tokens.access_token) is not None
        form["client_secret"] = registered["client_secret"]
        assert client.post("/revoke", data=form).status_code == 200
        assert oauth.store.get("access", tokens.access_token) is None


def test_revocation_cannot_invalidate_another_clients_tokens(server, settings):
    _, _, oauth, app = server
    with TestClient(app, base_url=settings.public_url) as client:
        client_id = client.post(
            "/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "token_endpoint_auth_method": "none",
            },
        ).json()["client_id"]
        tokens = oauth.issue("different-client", ["qq-mail.read"])
        assert (
            client.post(
                "/revoke",
                data={
                    "client_id": client_id,
                    "token": tokens.access_token,
                },
            ).status_code
            == 200
        )
        assert oauth.store.get("access", tokens.access_token) is not None
