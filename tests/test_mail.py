from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from qq_mail_mcp.mail import Mailbox, MailError, mailbox_name, parse_message, prepare_message, quote


def test_chinese_mime_and_attachment_metadata():
    msg = EmailMessage()
    msg["Subject"] = "中文会议通知"
    msg["From"] = "alice@example.com"
    msg.set_content("明天下午三点见。", charset="gb18030")
    msg.add_attachment(b"dummy", maintype="application", subtype="pdf", filename="材料.pdf")
    value = parse_message(msg.as_bytes(), include_body=True)
    assert value["subject"] == "中文会议通知"
    assert "明天下午" in value["body"]
    assert value["attachments"][0]["name"] == "材料.pdf"
    assert value["content_is_untrusted"]


def test_html_is_returned_as_text_without_script_or_external_fetch():
    msg = EmailMessage()
    msg.set_content(
        '<p>你好</p><script>steal()</script><img src="https://example.com/a">', subtype="html"
    )
    result = parse_message(msg.as_bytes(), include_body=True)
    assert "你好" in result["body"]
    assert "steal" not in result["body"]
    assert "<img" not in result["body"]


def test_unknown_charset_does_not_crash():
    raw = b'Content-Type: text/plain; charset="unknown-xyz"\r\n\r\nhello'
    assert parse_message(raw, True)["body"] == "hello"


@pytest.mark.parametrize("value", ["a\r\nb", "a\x00b", "a" * 201])
def test_search_injection_is_rejected(value):
    with pytest.raises(MailError):
        quote(value)


def test_search_quotes_and_mailbox_encoding():
    assert quote('a"b\\c') == b'"a\\"b\\\\c"'
    assert mailbox_name("已发送") == b'"&XfJT0ZAB-"'


def test_header_injection_is_rejected():
    with pytest.raises(MailError):
        prepare_message("test@qq.com", ["alice@example.com"], "x\r\nBcc: b@example.com", "x")
    with pytest.raises(MailError):
        prepare_message("test@qq.com", ["alice@example.com\n"], "x", "x")


class FakeIMAP:
    size = 100
    validity = 123

    def __init__(self, *args, **kwargs):
        self.commands = []
        self.capabilities = ("IMAP4REV1", "ID")
        self.state = "AUTH"

    def login(self, email, code):
        return "OK", []

    def xatom(self, *args):
        self.commands.append(args)
        return "OK", []

    def select(self, folder, readonly=False):
        assert readonly
        self.commands.append(("select", folder, readonly))
        return "OK", [b"1"]

    def response(self, key):
        return "UIDVALIDITY", [str(self.validity).encode()]

    def uid(self, *args):
        self.commands.append(args)
        if args[0] == "SEARCH":
            return "OK", [b"1 2"]
        if args[-1] == "(RFC822.SIZE)":
            return "OK", [f"1 (RFC822.SIZE {self.size})".encode()]
        return "OK", [(b"1 (UID 2 FLAGS ()", b"Subject: hello\r\n\r\nbody"), b")"]

    def logout(self):
        return "BYE", []


def test_imap_list_search_and_read_are_readonly(monkeypatch):
    conn = FakeIMAP()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **kw: conn)
    mailbox = Mailbox("test@qq.com", "dummy")
    value = mailbox.list(limit=1)
    assert value["messages"][0]["uid"] == 2
    assert value["messages"][0]["uidvalidity"] == 123
    assert value["has_more"]
    assert mailbox.read(2, 123)["body"] == "body"
    fetches = [cmd for cmd in conn.commands if cmd[0] == "FETCH"]
    assert all("PEEK" in cmd[-1] or "SIZE" in cmd[-1] for cmd in fetches)
    mailbox.list(query="会议")
    assert ("SEARCH", "CHARSET", "UTF-8", b"ALL", b"TEXT", '"会议"'.encode()) in conn.commands


def test_stale_uid_and_oversized_mail_are_rejected(monkeypatch):
    conn = FakeIMAP()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **kw: conn)
    mailbox = Mailbox("test@qq.com", "dummy")
    with pytest.raises(MailError, match="标识已变化"):
        mailbox.read(1, 999)
    conn.size = 3 * 1024 * 1024
    with pytest.raises(MailError, match="超过 2 MiB"):
        mailbox.read(1, 123)


def test_smtp_keeps_bcc_only_in_envelope_and_uses_qq_sender(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            assert (host, port) == ("smtp.qq.com", 465)
            assert kwargs["context"].check_hostname

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, email, code):
            assert email == "test@qq.com"

        def send_message(self, message, from_addr, to_addrs):
            captured.update(raw=message.as_bytes(), sender=from_addr, recipients=to_addrs)
            return {"blocked@example.com": (550, b"refused")}

    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    draft = prepare_message(
        "test@qq.com",
        ["alice@example.com"],
        "中文主题",
        "中文正文",
        bcc=["hidden@example.com", "blocked@example.com"],
    )
    result = Mailbox("test@qq.com", "dummy").send(draft)
    msg = BytesParser(policy=policy.default).parsebytes(captured["raw"])
    assert msg["Bcc"] is None
    assert "hidden@example.com" in captured["recipients"]
    assert "中文正文" in msg.get_content()
    assert result["refused"] == ["blocked@example.com"]
