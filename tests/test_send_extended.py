import pytest
from starlette.testclient import TestClient
from test_direct_send import ARGS, capture_send, configure
from test_http import call

from qq_mail_mcp.auth import READ
from qq_mail_mcp.server import create_app


def test_receipt_survives_restart_is_client_bound_and_does_not_send(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        result = call(client, token, "send_email", ARGS)["structuredContent"]
        status = call(client, token, "get_send_status", {"request_id": ARGS["request_id"]})[
            "structuredContent"
        ]
        assert status["status"] == "accepted"
        assert status["receipt"]["accepted"] == result["accepted"]
        assert status["delivery_confirmed"] is False
        other = direct_server[2].issue("other", [READ]).access_token
        assert (
            call(client, other, "get_send_status", {"draft_id": status["draft_id"]})[
                "structuredContent"
            ]["status"]
            == "not_found"
        )
        assert call(client, token, "get_send_status", {})["isError"]
    with TestClient(create_app(direct_settings), base_url=direct_settings.public_url) as client:
        assert (
            call(client, token, "get_send_status", {"request_id": ARGS["request_id"]})[
                "structuredContent"
            ]["status"]
            == "accepted"
        )
    assert len(sent) == 1


def test_reply_direct_mode_deduplicates_and_keeps_thread(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)

    def reply(self, uid, uidvalidity, body, folder, reply_all):
        from qq_mail_mcp.mail import prepare_message

        draft = prepare_message(self.email, ["sender@example.com"], "Re: Hi", body)
        draft.update(in_reply_to="<original@example.com>")
        return draft

    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.reply", reply)
    args = {"request_id": "reply-1", "uid": 1, "uidvalidity": 123, "body": "Thanks"}
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        assert call(client, token, "reply_email", args)["structuredContent"]["status"] == "accepted"
        assert call(client, token, "reply_email", args)["structuredContent"]["sent_now"] is False
        assert call(client, token, "reply_email", {**args, "body": "changed"})["isError"]
    assert len(sent) == 1 and sent[0]["in_reply_to"] == "<original@example.com>"


def test_forward_never_silently_discards_truncated_body(
    direct_server, direct_settings, monkeypatch
):
    token = configure(direct_server)
    sent = []
    capture_send(monkeypatch, sent)
    monkeypatch.setattr("qq_mail_mcp.mail.Mailbox.read", lambda *_: {"body_truncated": True})
    with TestClient(direct_server[3], base_url=direct_settings.public_url) as client:
        result = call(
            client,
            token,
            "forward_email",
            {"request_id": "forward-1", "uid": 1, "uidvalidity": 123, "to": ["a@example.com"]},
        )
        assert result["isError"]
    assert sent == []


def test_attachment_preview_contains_hash_and_not_binary(server, settings):
    store, oauth = server[1:3]
    store.bind_mailbox("owner@qq.com", "dummy")
    token = oauth.issue("client-a", ["qq-mail.read", "qq-mail.send"]).access_token
    with TestClient(server[3], base_url=settings.public_url) as client:
        prepared = call(
            client,
            token,
            "prepare_email",
            {
                "to": ["a@example.com"],
                "subject": "File",
                "body": "Hi",
                "attachments": [
                    {"name": "hello.txt", "content_type": "text/plain", "data_base64": "aGVsbG8="}
                ],
            },
        )["structuredContent"]
        file = prepared["preview"]["attachments"][0]
        assert file["size"] == 5 and len(file["sha256"]) == 64
        assert "data_base64" not in file
        page = client.get("/confirm/" + prepared["draft_id"])
        assert "hello.txt" in page.text and file["sha256"] in page.text
        assert "aGVsbG8=" not in page.text


@pytest.mark.parametrize("approval", [True, False])
def test_forward_complete_body_and_attachment_obey_send_mode(
    server, settings, direct_server, direct_settings, monkeypatch, approval
):
    active, config = (server, settings) if approval else (direct_server, direct_settings)
    store, oauth = active[1:3]
    store.bind_mailbox("owner@qq.com", "dummy")
    token = oauth.issue("client-a", ["qq-mail.read", "qq-mail.send"]).access_token
    sent = []
    capture_send(monkeypatch, sent)
    monkeypatch.setattr(
        "qq_mail_mcp.mail.Mailbox.read",
        lambda *_: {
            "from": "sender@example.com",
            "to": "owner@qq.com",
            "date": "2026-09-30",
            "subject": "Report",
            "body": "Complete report",
            "body_truncated": False,
            "attachments": [{"part_id": "2", "encoded_size": 8}],
        },
    )
    monkeypatch.setattr(
        "qq_mail_mcp.mail.Mailbox.attachment",
        lambda *_: {"name": "hello.txt", "content_type": "text/plain", "data_base64": "aGVsbG8="},
    )
    with TestClient(active[3], base_url=config.public_url) as client:
        result = call(
            client,
            token,
            "forward_email",
            {
                "request_id": "forward-complete",
                "uid": 1,
                "uidvalidity": 123,
                "to": ["recipient@example.com"],
                "include_attachments": True,
            },
        )["structuredContent"]
        if approval:
            assert result["approval_required"] is True and not sent
            assert (
                store.draft(result["draft_id"], "client-a")["payload"]["attachments"][0]["name"]
                == "hello.txt"
            )
            assert "data_base64" not in result["preview"]["attachments"][0]
        else:
            assert result["status"] == "accepted" and len(sent) == 1
            assert "Complete report" in sent[0]["body"]
            assert sent[0]["attachments"][0]["data_base64"] == "aGVsbG8="
