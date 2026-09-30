import base64
import binascii
import hashlib
import imaplib
import quopri
import re
import smtplib
import ssl
from contextlib import contextmanager
from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, make_msgid
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
        "reply_to": str(msg.get("Reply-To", "")),
        "references": str(msg.get("References", "")),
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
                conn.xatom("ID", '("name" "QQ Mail MCP" "version" "0.2.0")')
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
        self,
        folder="INBOX",
        limit=20,
        offset=0,
        unread_only=False,
        query="",
        sender="",
        since="",
        subject="",
        recipient="",
        before="",
        before_uid=None,
        uidvalidity=None,
    ):
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise MailError("limit 必须为 1–50，offset 必须为 0–10000")
        if before_uid is not None and (before_uid < 1 or not uidvalidity or offset):
            raise MailError("游标分页需要有效 before_uid、uidvalidity，且 offset 为 0")
        criteria = [b"UNSEEN" if unread_only else b"ALL"]
        for key, value in (
            (b"TEXT", query),
            (b"FROM", sender),
            (b"SUBJECT", subject),
            (b"TO", recipient),
        ):
            if value:
                criteria.extend([key, quote(value)])
        for key, value in ((b"SINCE", since), (b"BEFORE", before)):
            if value:
                try:
                    day = date.fromisoformat(value)
                except ValueError as exc:
                    raise MailError("日期必须为 YYYY-MM-DD") from exc
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
                criteria.extend([key, f"{day.day:02}-{months[day.month - 1]}-{day.year}".encode()])
        with self.connection(folder) as conn:
            validity = self.validity(conn)
            if uidvalidity is not None and validity != uidvalidity:
                raise MailError("邮件文件夹标识已变化，请重新查询列表")
            if before_uid is not None:
                if before_uid == 1:
                    return {"messages": [], "total": 0, "has_more": False, "next_cursor": None}
                criteria.extend([b"UID", f"1:{before_uid - 1}".encode()])
            if any(any(byte > 127 for byte in item) for item in criteria):
                status, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
            else:
                status, data = conn.uid("SEARCH", None, *criteria)
            if status != "OK":
                raise MailError("邮件搜索失败，服务器可能不支持该搜索条件")
            ids = sorted({int(i) for i in (data[0] or b"").split()}, reverse=True)
            selected = ids[offset : offset + limit]
            messages = []
            if selected:
                status, records = conn.uid(
                    "FETCH",
                    ",".join(map(str, selected)),
                    "(UID FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)])",
                )
                if status != "OK":
                    raise MailError("读取邮件列表失败")
                for record in records:
                    if isinstance(record, tuple):
                        match = re.search(rb"\bUID (\d+)", record[0])
                        if not match or int(match[1]) not in selected:
                            continue
                        item = parse_message(record[1])
                        item.update(
                            uid=int(match[1]),
                            uidvalidity=validity,
                            folder=folder,
                            unread=b"\\Seen" not in record[0],
                        )
                        messages.append(item)
            messages.sort(key=lambda item: item["uid"], reverse=True)
            has_more = offset + limit < len(ids)
            return {
                "messages": messages,
                "total": len(ids),
                "offset": offset,
                "has_more": has_more,
                "next_cursor": {
                    "before_uid": selected[-1],
                    "uidvalidity": validity,
                    "folder": folder,
                }
                if has_more and selected
                else None,
                "content_is_untrusted": True,
            }

    def _parts(self, conn, uid, uidvalidity):
        from .mime import parts, structure

        if uid < 1 or uidvalidity < 1:
            raise MailError("uid 和 uidvalidity 必须为正整数")
        if self.validity(conn) != uidvalidity:
            raise MailError("邮件文件夹标识已变化，请重新查询列表")
        status, records = conn.uid("FETCH", str(uid), "(BODYSTRUCTURE)")
        if status != "OK":
            raise MailError("读取邮件结构失败")
        raw = b" ".join(
            b"\r\n".join(record) if isinstance(record, tuple) else record
            for record in records
            if record is not None
        )
        return parts(structure(raw))

    @staticmethod
    def _fetch(conn, uid, section, limit):
        status, records = conn.uid("FETCH", str(uid), f"(BODY.PEEK[{section}]<0.{limit}>)")
        if status != "OK":
            raise MailError("读取邮件内容失败")
        raw = next((r[1] for r in records if isinstance(r, tuple)), None)
        if raw is None or len(raw) > limit:
            raise MailError("邮件内容不存在或超过读取限制")
        return raw

    @staticmethod
    def _decode(raw, encoding):
        try:
            if encoding.upper() == "BASE64":
                return base64.b64decode(raw)
            if encoding.upper() == "QUOTED-PRINTABLE":
                return quopri.decodestring(raw)
            return raw
        except (ValueError, binascii.Error) as exc:
            raise MailError("邮件内容编码不正确") from exc

    def read(self, uid, uidvalidity, folder="INBOX", body_offset=0, body_limit=30000):
        if not 0 <= body_offset <= 2000000 or not 1 <= body_limit <= 30000:
            raise MailError("正文 offset 必须为 0–2000000，limit 为 1–30000")
        with self.connection(folder) as conn:
            found = self._parts(conn, uid, uidvalidity)
            result = parse_message(self._fetch(conn, uid, "HEADER", 65536))
            candidates = [
                p
                for p in found
                if not p["attachment"] and p["content_type"] in ("text/plain", "text/html")
            ]
            chosen = next(
                (p for p in candidates if p["content_type"] == "text/plain"),
                candidates[0] if candidates else None,
            )
            body, source_truncated = "", False
            if chosen:
                limit = 2 * 1024 * 1024
                raw = self._fetch(conn, uid, chosen["part_id"], limit)
                source_truncated = chosen["encoded_size"] > limit
                if source_truncated and chosen["encoding"].upper() == "BASE64":
                    raw = re.sub(rb"\s+", b"", raw)
                    raw = raw[: len(raw) - len(raw) % 4]
                raw = self._decode(raw, chosen["encoding"])
                try:
                    body = raw.decode(chosen["charset"], "replace")
                except LookupError:
                    body = raw.decode("utf-8", "replace")
                if chosen["content_type"] == "text/html":
                    parser = PlainHTML()
                    parser.feed(body)
                    body = "".join(parser.parts)
            end = body_offset + body_limit
            result.update(
                uid=uid,
                uidvalidity=uidvalidity,
                folder=folder,
                body=body[body_offset:end],
                body_truncated=end < len(body) or source_truncated,
                source_truncated=source_truncated,
                next_body_offset=end if end < len(body) else None,
                attachments=[
                    {k: p[k] for k in ("part_id", "name", "content_type", "encoded_size")}
                    for p in found
                    if p["attachment"] or p["content_type"] not in ("text/plain", "text/html")
                ],
                content_is_untrusted=True,
            )
            return result

    def attachment(self, uid, uidvalidity, part_id, folder="INBOX"):
        if not re.fullmatch(r"[1-9][0-9]*(?:\.[1-9][0-9]*)*", part_id):
            raise MailError("part_id 必须来自 read_email 的附件列表")
        with self.connection(folder) as conn:
            found = self._parts(conn, uid, uidvalidity)
            part = next(
                (
                    p
                    for p in found
                    if p["part_id"] == part_id
                    and (p["attachment"] or p["content_type"] not in ("text/plain", "text/html"))
                ),
                None,
            )
            if not part:
                raise MailError("附件不存在")
            if part["encoded_size"] > 8 * 1024 * 1024:
                raise MailError("附件编码后超过 8 MiB，请在 QQ 邮箱下载")
            raw = self._fetch(conn, uid, part_id, 8 * 1024 * 1024)
            if len(raw) != part["encoded_size"]:
                raise MailError("附件读取不完整")
            data = self._decode(raw, part["encoding"])
            return {
                "name": part["name"] or "attachment",
                "content_type": part["content_type"],
                "size": len(data),
                "data_base64": base64.b64encode(data).decode(),
                "content_is_untrusted": True,
            }

    def reply(self, uid, uidvalidity, body, folder="INBOX", reply_all=False):
        with self.connection(folder) as conn:
            self._parts(conn, uid, uidvalidity)
            raw = self._fetch(conn, uid, "HEADER", 65536)
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        to = [addr for _, addr in getaddresses([str(msg.get("Reply-To") or msg.get("From", ""))])]
        cc = (
            [addr for _, addr in getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))])]
            if reply_all
            else []
        )
        to = list(dict.fromkeys(a for a in to if a.casefold() != self.email.casefold()))
        cc = list(
            dict.fromkeys(
                a
                for a in cc
                if a.casefold() != self.email.casefold()
                and a.casefold() not in {x.casefold() for x in to}
            )
        )
        if not to and cc:
            to, cc = [cc[0]], cc[1:]
        subject = str(msg.get("Subject", ""))
        draft = prepare_message(
            self.email,
            to,
            subject if subject.lower().startswith("re:") else "Re: " + subject,
            body,
            cc,
        )
        ids = re.findall(r"<[^<>\s]+>", str(msg.get("Message-ID", "")))
        references = re.findall(r"<[^<>\s]+>", str(msg.get("References", "")))
        if ids:
            draft.update(in_reply_to=ids[0], references=" ".join((references + [ids[0]])[-20:]))
        return draft

    def send(self, draft):
        msg = EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(draft["to"])
        if draft["cc"]:
            msg["Cc"] = ", ".join(draft["cc"])
        msg["Subject"] = draft["subject"]
        msg["Message-ID"] = draft["message_id"]
        msg["Date"] = draft["date"]
        for header, key in (("In-Reply-To", "in_reply_to"), ("References", "references")):
            if draft.get(key):
                msg[header] = draft[key]
        msg.set_content(draft["body"])
        for attachment in draft.get("attachments", []):
            main, sub = attachment["content_type"].split("/", 1)
            msg.add_attachment(
                base64.b64decode(attachment["data_base64"]),
                maintype=main,
                subtype=sub,
                filename=attachment["name"],
            )
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


def attach_files(payload, attachments=None):
    files, total = [], 0
    for item in attachments or []:
        name = item.get("name", "")
        kind = item.get("content_type", "application/octet-stream")
        if not name or len(name) > 200 or any(c in name for c in "/\\\r\n\x00"):
            raise MailError("附件名称不正确")
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", kind):
            raise MailError("附件类型不正确")
        try:
            data = base64.b64decode(item.get("data_base64", ""), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MailError("附件必须为有效 Base64") from exc
        total += len(data)
        if total > 5 * 1024 * 1024 or len(files) >= 5:
            raise MailError("最多 5 个附件，合计不超过 5 MiB")
        files.append(
            {"name": name, "content_type": kind, "data_base64": base64.b64encode(data).decode()}
        )
    if files:
        payload["attachments"] = files
    return payload


def draft_preview(payload):
    preview = {k: v for k, v in payload.items() if k != "attachments"}
    if payload.get("attachments"):
        preview["attachments"] = [
            {
                "name": item["name"],
                "content_type": item["content_type"],
                "size": len(base64.b64decode(item["data_base64"])),
                "sha256": hashlib.sha256(base64.b64decode(item["data_base64"])).hexdigest(),
            }
            for item in payload["attachments"]
        ]
    return preview
