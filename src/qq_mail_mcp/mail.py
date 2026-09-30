import imaplib
import re
import smtplib
import ssl
from contextlib import contextmanager
from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid
from html.parser import HTMLParser


class MailError(Exception):
    """A safe user-facing mail error; never include raw server replies or credentials."""


def address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254:
        raise MailError("邮箱地址格式不正确")
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        raise MailError("请使用不带显示名称的邮箱地址，例如 someone@example.com")
    return value


def account(email: str, auth_code: str):
    email = address(email)
    if email.rsplit("@", 1)[1].lower() not in ("qq.com", "foxmail.com"):
        raise MailError("仅支持 QQ 邮箱和 Foxmail 邮箱")
    if not auth_code or len(auth_code) > 256 or any(c in auth_code for c in "\r\n\x00"):
        raise MailError("请填写 QQ 邮箱生成的 IMAP/SMTP 授权码")
    return email, auth_code


def quote(value: str):
    if len(value) > 200 or any(c in value for c in "\r\n\x00"):
        raise MailError("搜索条件或文件夹名称不正确")
    return ('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"').encode()


def mailbox_name(value: str):
    """Encode IMAP modified UTF-7 rather than assuming UTF8=ACCEPT."""
    import base64

    result, run = [], []

    def flush():
        if run:
            encoded = base64.b64encode("".join(run).encode("utf-16be")).decode()
            result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            run.clear()

    for char in value:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            run.append(char)
    flush()
    return quote("".join(result))


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li", "tr") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def text_part(part):
    raw = part.get_payload(decode=True) or b""
    try:
        return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def parse_message(raw: bytes, include_body=False):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    result = {
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
    }
    if include_body:
        part = msg.get_body(preferencelist=("plain", "html"))
        body = text_part(part) if part else ""
        if part and part.get_content_type() == "text/html":
            parser = PlainHTML()
            parser.feed(body)
            body = "".join(parser.parts)
        result.update(
            body=body[:30000],
            body_truncated=len(body) > 30000,
            attachments=[
                {"name": p.get_filename() or "未命名附件", "content_type": p.get_content_type()}
                for p in msg.iter_attachments()
            ],
            content_is_untrusted=True,
        )
    return result


class Mailbox:
    def __init__(self, email: str, auth_code: str):
        self.email, self.auth_code = account(email, auth_code)

    @contextmanager
    def connection(self, folder="INBOX"):
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(
                "imap.qq.com", 993, ssl_context=ssl.create_default_context(), timeout=20
            )
            conn.login(self.email, self.auth_code)
            if "ID" in conn.capabilities:
                conn.xatom("ID", '("name" "QQ Mail MCP" "version" "0.1.0")')
            status, _ = conn.select(mailbox_name(folder), readonly=True)
            if status != "OK":
                raise MailError("无法打开指定邮件文件夹")
            yield conn
        except imaplib.IMAP4.error as exc:
            raise MailError("QQ 邮箱 IMAP 请求失败，请检查服务开关、授权码或搜索条件") from exc
        except (OSError, TimeoutError) as exc:
            raise MailError("无法连接 QQ 邮箱，请检查网络及 993 端口") from exc
        finally:
            if conn:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

    @staticmethod
    def validity(conn):
        _, values = conn.response("UIDVALIDITY")
        if not values or not values[0]:
            raise MailError("服务器未返回 UIDVALIDITY，不能安全定位邮件")
        return int(values[0])

    def check(self):
        with self.connection():
            return {"email": self.email, "imap_connected": True}

    def list(
        self, folder="INBOX", limit=20, offset=0, unread_only=False, query="", sender="", since=""
    ):
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise MailError("limit 必须为 1–50，offset 必须为 0–10000")
        criteria = [b"UNSEEN" if unread_only else b"ALL"]
        if query:
            criteria.extend([b"TEXT", quote(query)])
        if sender:
            criteria.extend([b"FROM", quote(sender)])
        if since:
            try:
                day = date.fromisoformat(since)
            except ValueError as exc:
                raise MailError("since 必须为 YYYY-MM-DD") from exc
            months = (
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            )
            criteria.extend([b"SINCE", f"{day.day:02}-{months[day.month - 1]}-{day.year}".encode()])
        with self.connection(folder) as conn:
            validity = self.validity(conn)
            if any(any(byte > 127 for byte in item) for item in criteria):
                status, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
            else:
                status, data = conn.uid("SEARCH", None, *criteria)
            if status != "OK":
                raise MailError("邮件搜索失败，服务器可能不支持该搜索条件")
            ids = (data[0] or b"").split()[::-1]
            selected = ids[offset : offset + limit]
            messages = []
            for uid in selected:
                status, records = conn.uid(
                    "FETCH",
                    uid,
                    "(UID FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)])",
                )
                if status != "OK":
                    raise MailError("读取邮件列表失败")
                for record in records:
                    if isinstance(record, tuple):
                        item = parse_message(record[1])
                        item.update(
                            uid=int(uid),
                            uidvalidity=validity,
                            folder=folder,
                            unread=b"\\Seen" not in record[0],
                        )
                        messages.append(item)
            return {
                "messages": messages,
                "total": len(ids),
                "offset": offset,
                "has_more": offset + limit < len(ids),
                "content_is_untrusted": True,
            }

    def read(self, uid: int, uidvalidity: int, folder="INBOX"):
        if uid < 1 or uidvalidity < 1:
            raise MailError("uid 和 uidvalidity 必须是正整数，请先查询邮件列表")
        with self.connection(folder) as conn:
            if self.validity(conn) != uidvalidity:
                raise MailError("邮件文件夹标识已变化，请重新查询列表")
            status, sizes = conn.uid("FETCH", str(uid), "(RFC822.SIZE)")
            size = next(
                (
                    re.search(rb"RFC822.SIZE (\d+)", x)
                    for x in sizes
                    if isinstance(x, bytes) and b"RFC822.SIZE" in x
                ),
                None,
            )
            if status != "OK" or size is None:
                raise MailError("邮件不存在或已被移除")
            if int(size.group(1)) > 2 * 1024 * 1024:
                raise MailError("邮件超过 2 MiB（含附件），请到 QQ 邮箱查看")
            status, records = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if status != "OK":
                raise MailError("读取邮件失败")
            raw = next((x[1] for x in records if isinstance(x, tuple)), None)
            if raw is None:
                raise MailError("邮件不存在或已被移除")
            result = parse_message(raw, include_body=True)
            result.update(uid=uid, uidvalidity=uidvalidity, folder=folder)
            return result

    def send(self, draft):
        msg = EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(draft["to"])
        if draft["cc"]:
            msg["Cc"] = ", ".join(draft["cc"])
        msg["Subject"] = draft["subject"]
        msg["Message-ID"] = draft["message_id"]
        msg["Date"] = draft["date"]
        msg.set_content(draft["body"])
        recipients = list(dict.fromkeys(draft["to"] + draft["cc"] + draft["bcc"]))
        with smtplib.SMTP_SSL(
            "smtp.qq.com", 465, timeout=20, context=ssl.create_default_context()
        ) as conn:
            conn.login(self.email, self.auth_code)
            refused = conn.send_message(msg, from_addr=self.email, to_addrs=recipients)
        return {
            "message_id": draft["message_id"],
            "accepted": [r for r in recipients if r not in refused],
            "refused": list(refused),
        }


def prepare_message(email, to, subject, body, cc=None, bcc=None):
    from email.utils import formatdate

    to, cc, bcc = to or [], cc or [], bcc or []
    if not to or len(to) + len(cc) + len(bcc) > 50:
        raise MailError("至少需要一个收件人，总收件人数不能超过 50")
    for recipient in to + cc + bcc:
        address(recipient)
    if not subject.strip() or len(subject) > 998 or any(c in subject for c in "\r\n\x00"):
        raise MailError("主题不能为空、不能包含换行，且不能超过 998 个字符")
    if len(body) > 100000 or "\x00" in body:
        raise MailError("正文不能超过 100000 个字符或包含空字符")
    return {
        "from": email,
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body": body,
        "message_id": make_msgid(domain="qq.com"),
        "date": formatdate(),
    }
