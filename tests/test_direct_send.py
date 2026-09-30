import smtplib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from starlette.testclient import TestClient
from test_http import call, rpc

from qq_mail_mcp.auth import READ, SEND
from qq_mail_mcp.mail import prepare_message
from qq_mail_mcp.server import create_app
from qq_mail_mcp.store import Store

ARGS = {
    "request_id": "example-request-1",
    "to": ["alice@example.com"],
    "cc": ["bob@example.com"],
    "bcc": ["carol@example.com"],
    "subject": "直接发信测试",
    "body": "这是一封测试邮件。",
}


def configure(server):
    _, store, oauth, _ = server
    store.bind_mailbox("test@qq.com", "dummy")
    return oauth.issue("client-a", [READ, SEND]).access_token


def capture_send(monkeypatch, sent):
    def send(self, payload):
        sent.append(payload)
        return {
            "message_id": payload["message_id"],
            "accepted": payload["to"] + payload["cc"] + payload["bcc"],
            "refused": [],
        }

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.send", send)


def test_direct_send_without_approval_is_durable_and_single_use(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        status = call(client, token, "mailbox_status")["structuredContent"]
        assert status["send_mode"] == "direct"
        assert status["send_approval_required"] is False
        result = call(client, token, "send_email", ARGS)["structuredContent"]
        assert result["status"] == "accepted" and result["sent_now"] is True
        assert len(result["accepted"]) == 3
        repeated = call(client, token, "send_email", ARGS)["structuredContent"]
        assert repeated["status"] == "sent" and repeated["sent_now"] is False
        assert repeated["message_id"] == result["message_id"]
        modified = call(client, token, "send_email", {**ARGS, "body": "不同的正文"})
        assert modified["isError"] is True
    with TestClient(create_app(direct_settings), base_url=direct_settings.public_url) as client:
        assert call(client, token, "send_email", ARGS)["structuredContent"]["status"] == "sent"
    assert len(sent) == 1
    assert sent[0]["bcc"] == ARGS["bcc"]
    assert ARGS["body"].encode() not in direct_settings.database.read_bytes()


def test_direct_mode_prepared_mail_does_not_return_approval_url(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        prepared = call(
            client, token, "prepare_email", {k: v for k, v in ARGS.items() if k != "request_id"}
        )["structuredContent"]
        assert prepared["approval_required"] is False and "approval_url" not in prepared
        args = {"draft_id": prepared["draft_id"]}
        assert (
            call(client, token, "send_prepared_email", args)["structuredContent"]["status"]
            == "accepted"
        )
        assert (
            call(client, token, "send_prepared_email", args)["structuredContent"]["sent_now"]
            is False
        )
    assert len(sent) == 1


def test_concurrent_direct_requests_only_attempt_smtp_once(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []

    def send(self, payload):
        sent.append(payload)
        time.sleep(0.15)
        return {"message_id": payload["message_id"], "accepted": payload["to"], "refused": []}

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.send", send)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: call(client, token, "send_email", ARGS), range(4)))
    assert all(not r.get("isError") for r in results)
    assert sum(r["structuredContent"]["sent_now"] is True for r in results) == 1
    assert len(sent) == 1


def test_uncertain_direct_send_cannot_retry_after_restart(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    attempts = []

    def timeout(self, payload):
        attempts.append(payload)
        raise smtplib.SMTPServerDisconnected("dummy")

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.send", timeout)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        first = call(client, token, "send_email", ARGS)["structuredContent"]
        assert first["status"] == "unknown" and first["sent_now"] is None
    with TestClient(create_app(direct_settings), base_url=direct_settings.public_url) as client:
        again = call(client, token, "send_email", ARGS)["structuredContent"]
        assert again["status"] == "unknown" and again["sent_now"] is None
        assert again["message_id"] == first["message_id"]
    assert len(attempts) == 1


def test_read_scope_cannot_direct_send(direct_server, direct_settings, monkeypatch):
    configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    token = direct_server[2].issue("reader", [READ]).access_token
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        assert call(client, token, "send_email", ARGS)["isError"] is True
    assert sent == []
    with direct_server[1].connect() as db:
        assert db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", [{"request_id": "bad\nid"}, {"to": ["bad-address"]}])
def test_invalid_direct_send_does_not_create_or_send(
    direct_server, direct_settings, monkeypatch, invalid
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        assert call(client, token, "send_email", {**ARGS, **invalid})["isError"] is True
    assert sent == []
    with direct_server[1].connect() as db:
        assert db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_approval_mode_and_disabled_sending_do_not_expose_direct_tool(settings):
    for config in (settings, replace(settings, allow_send=False, require_send_approval=False)):
        from qq_mail_mcp.server import build_server

        _, _, oauth = build_server(config)
        token = oauth.issue("client-a", [READ, SEND]).access_token
        with TestClient(create_app(config), base_url=config.public_url) as client:
            tools = rpc(client, token, "tools/list")["result"]["tools"]
            assert "send_email" not in {t["name"] for t in tools}


def test_idempotency_keys_are_client_bound_and_cleared_on_mailbox_change(direct_settings):
    store = Store(direct_settings)
    store.bind_mailbox("test@qq.com", "dummy")
    payload = prepare_message("test@qq.com", ["alice@example.com"], "test", "body")
    a = store.direct_draft("client-a", "request-1", payload)
    assert store.direct_draft("client-a", "request-1", payload) == a
    b = store.direct_draft("client-b", "request-1", payload)
    assert a != b and store.draft(a, "client-b") is None
    store.bind_mailbox("second@qq.com", "new-code")
    assert store.draft(a) is None and store.draft(b) is None
    with store.connect() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM records WHERE kind='send_request'").fetchone()[0] == 0
        )


def test_return_to_approval_mode_requires_approval_for_pending_drafts(
    direct_server, direct_settings, settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        draft = call(
            client, token, "prepare_email", {k: v for k, v in ARGS.items() if k != "request_id"}
        )["structuredContent"]
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        result = call(client, token, "send_prepared_email", {"draft_id": draft["draft_id"]})
        assert result["structuredContent"]["status"] == "pending"
    assert sent == []
