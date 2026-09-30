"""Bounded IMAP BODYSTRUCTURE parsing and MIME part discovery."""

import re
from email import policy
from email.parser import BytesParser

from .mail import MailError


def structure(data: bytes):
    if len(data) > 65536:
        raise MailError("邮件结构过大")
    match = re.search(rb"\bBODYSTRUCTURE\s+", data, re.I)
    if not match:
        raise MailError("服务器未返回邮件结构")
    pos, nodes = match.end(), 0

    def parse(depth=0):
        nonlocal pos, nodes
        nodes += 1
        if depth > 32 or nodes > 4096:
            raise MailError("邮件结构过于复杂")
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        if pos >= len(data):
            raise MailError("邮件结构不完整")
        char = data[pos : pos + 1]
        pos += 1
        if char == b"(":
            value = []
            while True:
                while pos < len(data) and data[pos : pos + 1].isspace():
                    pos += 1
                if pos >= len(data):
                    raise MailError("邮件结构不完整")
                if data[pos : pos + 1] == b")":
                    pos += 1
                    return value
                value.append(parse(depth + 1))
        if char == b'"':
            value = bytearray()
            while pos < len(data):
                char = data[pos : pos + 1]
                pos += 1
                if char == b'"':
                    return value.decode("utf-8", "replace")
                if char == b"\\":
                    if pos >= len(data):
                        break
                    char = data[pos : pos + 1]
                    pos += 1
                value.extend(char)
            raise MailError("邮件结构字符串不完整")
        if char == b"{":
            match = re.match(rb"(\d+)\}\r\n", data[pos:])
            if not match:
                raise MailError("邮件结构字面量不完整")
            size = int(match[1])
            pos += match.end()
            if pos + size > len(data):
                raise MailError("邮件结构字面量不完整")
            value = data[pos : pos + size].decode("utf-8", "replace")
            pos += size
            return value
        start = pos - 1
        while pos < len(data) and data[pos : pos + 1] not in b" ()\r\n":
            pos += 1
        value = data[start:pos].decode("ascii", "replace")
        return None if value.upper() == "NIL" else int(value) if value.isdigit() else value

    value = parse()
    if not isinstance(value, list):
        raise MailError("邮件结构不正确")
    return value


def params(value):
    if not isinstance(value, list) or len(value) % 2:
        return {}
    return {str(value[i]).lower(): str(value[i + 1] or "") for i in range(0, len(value), 2)}


def parts(tree, prefix=""):
    if not tree:
        raise MailError("邮件结构不正确")
    if isinstance(tree[0], list):
        found = []
        for i, child in enumerate(tree):
            if not isinstance(child, list):
                break
            found.extend(parts(child, f"{prefix}.{i + 1}".lstrip(".")))
        return found
    if len(tree) < 7 or not isinstance(tree[6], int) or tree[6] < 0:
        raise MailError("邮件结构不正确")
    kind = f"{tree[0]}/{tree[1]}".lower()
    disposition_index = (
        9 if str(tree[0]).lower() == "text" else 11 if kind == "message/rfc822" else 8
    )
    disposition = tree[disposition_index] if len(tree) > disposition_index else None
    disp = str(disposition[0]).lower() if isinstance(disposition, list) and disposition else ""
    values = params(tree[2])
    filename_values = (
        params(disposition[1]) if isinstance(disposition, list) and len(disposition) > 1 else {}
    )
    # Let the stdlib decode RFC 2231 continuations and MIME-encoded names.
    headers = f"Content-Type: {kind}"
    for key, value in values.items():
        headers += (
            "; "
            + key
            + '="'
            + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
            + '"'
        )
    headers += "\r\nContent-Disposition: " + (disp or "inline")
    for key, value in filename_values.items():
        headers += (
            "; "
            + key
            + '="'
            + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
            + '"'
        )
    message = BytesParser(policy=policy.default).parsebytes((headers + "\r\n\r\n").encode())
    name = message.get_filename()
    return [
        {
            "part_id": prefix or "1",
            "content_type": kind,
            "encoding": str(tree[5]),
            "encoded_size": tree[6],
            "charset": values.get("charset", "utf-8"),
            "name": name,
            "attachment": disp == "attachment" or bool(name),
        }
    ]
