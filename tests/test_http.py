import base64
import hashlib
import re
import secrets
import smtplib
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from qq_mail_mcp.auth import READ, SEND


def headers(token):
    return {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}


def rpc(client, token, method, params=None):
    response = client.post(
        "/mcp",
        headers=headers(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def call(client, token, name, arguments=None):
    return rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})["result"]


def csrf(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)


def test_oauth_pkce_refresh_and_authenticated_discovery(server, settings):
    _, _, _, app = server
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    with TestClient(app, base_url=settings.public_url) as client:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        resource = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource.status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "client_name": "Test client",
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert registration.status_code == 201, registration.text
        client_id = registration.json()["client_id"]
        authorized = client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": "https://example.com/callback",
                "state": "caller-state",
                "code_challenge": challenge.decode(),
                "code_challenge_method": "S256",
                "scope": f"{READ} {SEND}",
                "resource": settings.public_url + "/mcp",
            },
            follow_redirects=False,
        )
        assert authorized.status_code in (302, 307), authorized.text
        consent_path = urlsplit(authorized.headers["location"]).path
        form = client.get(consent_path)
        assert "form-action 'self' https://example.com;" in form.headers["content-security-policy"]
        rejected = client.post(consent_path, data={"admin_password": "wrong", "csrf": csrf(form)})
        assert rejected.status_code == 403
        accepted = client.post(
            consent_path,
            data={
                "admin_password": settings.admin_password,
                "csrf": csrf(form),
            },
            follow_redirects=False,
        )
        assert accepted.status_code == 303, accepted.text
        query = parse_qs(urlsplit(accepted.headers["location"]).query)
        assert query["state"] == ["caller-state"]
        token_form = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": query["code"][0],
            "code_verifier": "x" * 43,
            "redirect_uri": "https://example.com/callback",
        }
        assert client.post("/token", data=token_form).status_code == 400
        token_form["code_verifier"] = verifier
        issued = client.post("/token", data=token_form)
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert client.post("/token", data=token_form).status_code == 400
        initialized = rpc(
            client,
            tokens["access_token"],
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert initialized["result"]["serverInfo"]["name"] == "QQ Mail"
        discovery = rpc(client, tokens["access_token"], "tools/list")
        names = {t["name"] for t in discovery["result"]["tools"]}
        assert names == {
            "mailbox_status",
            "list_emails",
            "search_emails",
            "read_email",
            "prepare_email",
            "send_prepared_email",
            "download_attachment",
            "get_send_status",
            "reply_email",
            "forward_email",
        }
        status = call(client, tokens["access_token"], "mailbox_status")
        assert status["structuredContent"]["configured"] is False
        refreshed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        assert (
            client.post("/mcp", headers=headers(tokens["access_token"]), json={}).status_code == 401
        )
        assert (
            client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": tokens["refresh_token"],
                },
            ).status_code
            == 400
        )


def test_sending_needs_browser_approval_and_cannot_duplicate(server, settings, monkeypatch):
    _, store, oauth, app = server
    store.put("mailbox", "owner", {"email": "test@qq.com", "auth_code": "dummy"})
    token = oauth.issue("client-a", [READ, SEND]).access_token
    sent = []

    def fake_send(self, payload):
        sent.append(payload)
        return {"message_id": payload["message_id"], "accepted": payload["to"], "refused": []}

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.send", fake_send)
    with TestClient(app, base_url=settings.public_url) as client:
        prepared = call(
            client,
            token,
            "prepare_email",
            {
                "to": ["alice@example.com"],
                "subject": "会议",
                "body": "明天三点见",
            },
        )["structuredContent"]
        draft_id = prepared["draft_id"]
        unapproved = call(client, token, "send_prepared_email", {"draft_id": draft_id})
        assert unapproved["structuredContent"]["status"] == "pending"
        assert sent == []
        path = "/confirm/" + draft_id
        form = client.get(path)
        assert "明天三点见" in form.text
        assert (
            client.post(
                path,
                data={
                    "admin_password": settings.admin_password,
                    "csrf": csrf(form),
                },
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                data={
                    "admin_password": settings.admin_password,
                    "csrf": csrf(form),
                },
            ).status_code
            == 200
        )
        other_token = oauth.issue("client-b", [READ, SEND]).access_token
        assert call(client, other_token, "send_prepared_email", {"draft_id": draft_id})["isError"]
        result = call(client, token, "send_prepared_email", {"draft_id": draft_id})
        assert result["structuredContent"]["status"] == "accepted"
        assert (
            call(client, token, "send_prepared_email", {"draft_id": draft_id})["structuredContent"][
                "sent_now"
            ]
            is False
        )
        assert len(sent) == 1
        assert sent[0]["body"] == "明天三点见"
        assert (
            client.post(
                path, data={"admin_password": settings.admin_password, "csrf": csrf(form)}
            ).status_code
            == 409
        )


def test_uncertain_smtp_cannot_be_retried(server, settings, monkeypatch):
    _, store, oauth, app = server
    store.put("mailbox", "owner", {"email": "test@qq.com", "auth_code": "dummy"})
    token = oauth.issue("client-a", [READ, SEND]).access_token
    attempted = []

    def timeout_send(self, payload):
        attempted.append(payload)
        raise smtplib.SMTPServerDisconnected("dummy")

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.send", timeout_send)
    with TestClient(app, base_url=settings.public_url) as client:
        draft = call(
            client,
            token,
            "prepare_email",
            {
                "to": ["alice@example.com"],
                "subject": "test",
                "body": "test",
            },
        )["structuredContent"]
        store.transition(draft["draft_id"], "pending", "approved")
        result = call(client, token, "send_prepared_email", {"draft_id": draft["draft_id"]})
        assert result["structuredContent"]["status"] == "unknown"
        call(client, token, "send_prepared_email", {"draft_id": draft["draft_id"]})
        assert len(attempted) == 1


async def test_oauth_revocation_invalidates_both_tokens(server):
    _, _, oauth, _ = server
    issued = oauth.issue("client-a", [READ])
    access = await oauth.load_access_token(issued.access_token)
    assert isinstance(access, AccessToken)
    await oauth.revoke_token(access)
    assert await oauth.load_access_token(issued.access_token) is None
    assert server[1].get("refresh", issued.refresh_token) is None


def test_read_scope_cannot_prepare_outgoing_email(server, settings):
    _, store, oauth, app = server
    store.put("mailbox", "owner", {"email": "test@qq.com", "auth_code": "dummy"})
    token = oauth.issue("client-a", [READ]).access_token
    with TestClient(app, base_url=settings.public_url) as client:
        result = call(
            client,
            token,
            "prepare_email",
            {
                "to": ["alice@example.com"],
                "subject": "test",
                "body": "test",
            },
        )
        assert result["isError"] is True
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_mailbox_setup_checks_credentials_and_encrypts_them(server, settings, monkeypatch):
    _, store, _, app = server
    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.check", lambda self: {"imap_connected": True})
    with TestClient(app, base_url=settings.public_url) as client:
        form = client.get("/")
        result = client.post(
            "/",
            data={
                "email": "test@qq.com",
                "auth_code": "dummy-test-secret",
                "admin_password": settings.admin_password,
                "csrf": csrf(form),
            },
        )
        assert result.status_code == 200
        assert store.get("mailbox", "owner")["email"] == "test@qq.com"
        assert b"dummy-test-secret" not in settings.database.read_bytes()


def test_request_body_limit(server, settings):
    app = server[3]
    with TestClient(app, base_url=settings.public_url) as client:
        assert client.post("/", content=b"x" * (256 * 1024 + 1)).status_code == 413


def test_owner_forms_preserve_browser_origin_and_reject_opaque_origins(
    server, settings, monkeypatch
):
    _, store, _, app = server
    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.check", lambda self: {"imap_connected": True})
    with TestClient(app, base_url=settings.public_url) as client:
        form = client.get("/")
        assert form.headers["referrer-policy"] == "same-origin"
        data = {
            "email": "test@qq.com",
            "auth_code": "dummy-test-secret",
            "admin_password": settings.admin_password,
            "csrf": csrf(form),
        }
        for origin in ("null", "https://evil.example"):
            assert client.post("/", data=data, headers={"Origin": origin}).status_code == 403
            assert store.get("mailbox", "owner") is None
        result = client.post("/", data=data, headers={"Origin": settings.public_url})
        assert result.status_code == 200
        assert store.get("mailbox", "owner")["email"] == "test@qq.com"


def test_callback_csp_does_not_interpolate_directives_or_wildcards():
    from qq_mail_mcp.web import page

    setup = page("Setup", "")
    assert "form-action 'self';" in setup.headers["content-security-policy"]
    callback = page("Consent", "", form_redirect="https://chatgpt.com/connector/oauth/test")
    assert "form-action 'self' https://chatgpt.com;" in callback.headers["content-security-policy"]
    ipv6 = page("Consent", "", form_redirect="http://[::1]:8000/callback")
    assert "form-action 'self' http://[::1]:8000;" in ipv6.headers["content-security-policy"]
    malicious = page("Consent", "", form_redirect="https://evil.example;script-src=*")
    assert "script-src=" not in malicious.headers["content-security-policy"]
    assert "*" not in malicious.headers["content-security-policy"]
