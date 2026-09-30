import base64
from email import policy
from email.parser import BytesParser

import pytest

from qq_mail_mcp.mail import Mailbox, MailError, attach_files, prepare_message
from qq_mail_mcp.mime import parts, structure

TEXT = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "BASE64" 12 1 NIL NIL NIL NIL)'
PDF = (
    b'("APPLICATION" "PDF" ("NAME" "report.pdf") NIL NIL "BASE64" 70000000 NIL '
    b'("ATTACHMENT" ("FILENAME" "report.pdf")) NIL NIL)'
)


class MIMEIMAP:
    capabilities = (b"IMAP4REV1",)
    tree = b"(" + TEXT + PDF + b' "MIXED" ("BOUNDARY" "test") NIL NIL)'
    header = (
        b"From: Alice <alice@example.com>\r\nReply-To: reply@example.com\r\n"
        b"To: owner@qq.com, bob@example.com\r\nCc: carol@example.com\r\n"
        b"Subject: Project\r\nMessage-ID: <original@example.com>\r\n"
        b"References: <parent@example.com>\r\n\r\n"
    )
    data = {"1": base64.b64encode(b"hello world!")}
    uidvalidity = 123

    def __init__(self):
        self.commands = []

    def login(self, *_):
        return "OK", []

    def select(self, folder, readonly=False):
        assert readonly
        return "OK", []

    def response(self, *_):
        return "UIDVALIDITY", [str(self.uidvalidity).encode()]

    def uid(self, *args):
        self.commands.append(args)
        if args[0] == "SEARCH":
            return "OK", [b"3 1 2"]
        if args[-1] == "(BODYSTRUCTURE)":
            return "OK", [b"1 (BODYSTRUCTURE " + self.tree + b")"]
        if "HEADER.FIELDS" in args[-1]:
            return "OK", [
                (f"1 (UID {i} FLAGS ())".encode(), self.header)
                for i in reversed(args[1].split(","))
            ]
        if "[HEADER]" in args[-1]:
            return "OK", [(b"1 (BODY[HEADER]", self.header), b")"]
        if "[1]" in args[-1]:
            return "OK", [(b"1 (BODY[1]", self.data["1"]), b")"]
        if "[2]" in args[-1]:
            return "OK", [(b"1 (BODY[2]", self.data["2"]), b")"]
        pytest.fail(f"Unexpected fetch: {args}")

    def logout(self):
        return "BYE", []


@pytest.fixture
def mailbox(monkeypatch):
    conn = MIMEIMAP()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **kw: conn)
    return Mailbox("owner@qq.com", "dummy"), conn


def test_large_attachment_does_not_block_body_or_download_it(mailbox):
    mail, conn = mailbox
    value = mail.read(1, 123)
    assert value["body"] == "hello world!"
    assert value["attachments"] == [
        {
            "part_id": "2",
            "name": "report.pdf",
            "content_type": "application/pdf",
            "encoded_size": 70000000,
        }
    ]
    assert not any("[2]" in cmd[-1] or "PEEK[]" in cmd[-1] for cmd in conn.commands)
    with pytest.raises(MailError, match="超过 8 MiB"):
        mail.attachment(1, 123, "2")


def test_nested_multipart_and_rfc2231_names():
    pdf = PDF.replace(b"70000000", b"4").replace(
        b'"FILENAME" "report.pdf"', b'"FILENAME*" "utf-8\'\'%E6%96%87%E6%A1%A3.pdf"'
    )
    tree = structure(b"1 (BODYSTRUCTURE ((" + TEXT + b' "ALTERNATIVE")' + pdf + b' "MIXED"))')
    found = parts(tree)
    assert [p["part_id"] for p in found] == ["1.1", "2"]
    assert found[1]["name"] == "文档.pdf"


def test_literal_strings_and_bounded_parser():
    raw = b'1 (BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" {5}\r\nUTF-8) NIL NIL "7BIT" 4 1))'
    assert parts(structure(raw))[0]["charset"] == "UTF-8"
    for raw in (
        b"missing",
        b"BODYSTRUCTURE (",
        b"BODYSTRUCTURE (" * 40,
        b'BODYSTRUCTURE ("unterminated',
        b"BODYSTRUCTURE " + b"x" * 65536,
    ):
        with pytest.raises(MailError):
            structure(raw)


def test_pagination_and_search_are_batched_and_cursor_checked(mailbox):
    mail, conn = mailbox
    result = mail.list(
        limit=2,
        subject="Project",
        recipient="bob@example.com",
        since="2026-09-01",
        before="2026-10-01",
    )
    assert [m["uid"] for m in result["messages"]] == [3, 2]
    assert result["next_cursor"]["before_uid"] == 2
    assert len([cmd for cmd in conn.commands if cmd[0] == "FETCH"]) == 1
    with pytest.raises(MailError, match="标识已变化"):
        mail.list(before_uid=2, uidvalidity=999)
    assert mail.list(before_uid=1, uidvalidity=123)["messages"] == []
    for kwargs in (
        {"since": "bad"},
        {"before_uid": 2},
        {"before_uid": 2, "uidvalidity": 123, "offset": 1},
    ):
        with pytest.raises(MailError):
            mail.list(**kwargs)


def test_body_pagination_never_marks_mail_seen(mailbox):
    mail, conn = mailbox
    result = mail.read(1, 123, body_limit=5)
    assert result["body"] == "hello" and result["next_body_offset"] == 5
    assert mail.read(1, 123, body_offset=5)["body"] == " world!"
    assert all("PEEK" in cmd[-1] or "BODYSTRUCTURE" in cmd[-1] for cmd in conn.commands)


def test_attachment_is_complete_and_identity_checked(mailbox):
    mail, conn = mailbox
    conn.tree = conn.tree.replace(b"70000000", b"8")
    conn.data = {**conn.data, "2": base64.b64encode(b"file")}
    result = mail.attachment(1, 123, "2")
    assert base64.b64decode(result["data_base64"]) == b"file"
    with pytest.raises(MailError):
        mail.attachment(1, 999, "2")
    with pytest.raises(MailError):
        mail.attachment(1, 123, "3")
    with pytest.raises(MailError):
        mail.attachment(1, 123, "2] FLAGS")
    conn.data["2"] = b"bad"
    with pytest.raises(MailError, match="不完整"):
        mail.attachment(1, 123, "2")


def test_reply_all_preserves_thread_and_excludes_owner(mailbox, monkeypatch):
    mail, _ = mailbox
    draft = mail.reply(1, 123, "Thanks", reply_all=True)
    assert draft["to"] == ["reply@example.com"]
    assert draft["cc"] == ["bob@example.com", "carol@example.com"]
    assert draft["in_reply_to"] == "<original@example.com>"
    assert draft["references"] == "<parent@example.com> <original@example.com>"
    assert draft["subject"] == "Re: Project"
    captured = []

    class SMTP:
        def __init__(self, *_a, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def login(self, *_):
            pass

        def send_message(self, message, **_):
            captured.append(message.as_bytes())
            return {}

    monkeypatch.setattr("smtplib.SMTP_SSL", SMTP)
    attach_files(
        draft, [{"name": "note.txt", "data_base64": "aGVsbG8=", "content_type": "text/plain"}]
    )
    mail.send(draft)
    message = BytesParser(policy=policy.default).parsebytes(captured[0])
    assert str(message["In-Reply-To"]) == "<original@example.com>"
    assert next(message.iter_attachments()).get_payload(decode=True) == b"hello"


@pytest.mark.parametrize(
    "item",
    [
        {"name": "../bad", "data_base64": "eA=="},
        {"name": "file", "data_base64": "not-base64"},
        {"name": "file", "data_base64": "eA==", "content_type": "text/plain\r\nX: bad"},
    ],
)
def test_bad_outbound_attachments_rejected(item):
    with pytest.raises(MailError):
        attach_files(prepare_message("owner@qq.com", ["a@example.com"], "hi", "hi"), [item])
