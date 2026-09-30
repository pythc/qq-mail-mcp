import asyncio
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from .mail import Mailbox, MailError, account


def page(title, content, status=200, form_redirect=None):
    form_sources = "'self'"
    if form_redirect:
        # Browser form-action also governs the POST's OAuth 303 redirect.
        # This URI has already passed registration and exact-URI validation.
        callback = urlsplit(form_redirect)
        # Encode CSP delimiters and wildcards instead of interpolating them.
        form_sources += " " + callback.scheme + "://" + quote(callback.netloc, safe="[]:.")
    return HTMLResponse(
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 邮箱 MCP</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f3f6fc;color:#14213d;margin:0}
main{max-width:720px;margin:6vh auto;padding:28px;background:white;border-radius:14px}
h1{font-size:26px;margin:0 0 20px}label{display:block;margin:18px 0 6px}
input,button{box-sizing:border-box;font:inherit;width:100%;padding:12px;border:1px solid
#94a3b8;border-radius:7px}button{background:#1248c4;color:white;border:0;margin-top:22px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fc;padding:18px}
a{color:#1248c4}small{color:#475569}@media(max-width:760px){main{margin:12px;padding:22px}}
</style><main><h1>"""
        + html.escape(title)
        + "</h1>"
        + content
        + "</main></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            # no-referrer makes native browser POST forms send Origin: null.
            # Preserve same-origin checks without leaking consent URLs off-site.
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            f"form-action {form_sources}; base-uri 'none'; frame-ancestors 'none'",
        },
    )


class OwnerPages:
    def __init__(self, settings, store, oauth, get_mailbox):
        self.settings, self.store, self.oauth = settings, store, oauth
        self.get_mailbox = get_mailbox

    def csrf(self, path):
        value = f"{int(time.time())}:{secrets.token_hex(16)}"
        signature = hmac.new(
            self.settings.admin_password.encode(), f"{path}:{value}".encode(), hashlib.sha256
        ).hexdigest()
        return value + ":" + signature

    async def verified_form(self, request):
        # Limit requests before form parsing; server additionally enforces a body-size cap.
        if request.headers.get("origin") not in (None, self.settings.public_url):
            return None
        form = await request.form(max_fields=8, max_files=0)
        csrf = str(form.get("csrf", ""))
        try:
            issued, nonce, signature = csrf.split(":")
            if not 0 <= time.time() - int(issued) <= 600:
                return None
        except ValueError:
            return None
        expected = hmac.new(
            self.settings.admin_password.encode(),
            f"{request.url.path}:{issued}:{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        supplied = str(form.get("admin_password", ""))
        if not hmac.compare_digest(supplied.encode(), self.settings.admin_password.encode()):
            # A deployment must also rate-limit this route at its reverse proxy.
            await asyncio.sleep(1)
            return None
        return form

    def owner_form(self, path, label, extra=""):
        return (
            f'<form method="post" action="{html.escape(path, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{self.csrf(path)}">'
            + extra
            + '<label for="password">插件管理密码</label>'
            '<input id="password" type="password" name="admin_password" '
            'required autocomplete="current-password">'
            f'<button type="submit">{html.escape(label)}</button></form>'
        )

    async def setup(self, request: Request):
        if request.method == "POST":
            form = await self.verified_form(request)
            if form is None:
                return page("验证失败", "管理密码不正确或表单已过期，请刷新页面重试。", 403)
            if self.settings.email:
                return page("邮箱由环境配置管理", "请在部署配置中修改邮箱。", 409)
            try:
                email, code = account(str(form.get("email", "")), str(form.get("auth_code", "")))
                await asyncio.to_thread(Mailbox(email, code).check)
                self.store.bind_mailbox(email, code)
            except MailError as exc:
                return page("绑定失败", html.escape(str(exc)) + '<p><a href="/">返回</a></p>', 400)
            return page(
                "邮箱已绑定",
                "已验证 IMAP 连接并加密保存授权码。"
                "<p>现在可以在 ChatGPT 中连接此服务器的 /mcp 地址。"
                "首次绑定或更换邮箱后，需要重新授权连接。</p>",
            )
        text = (
            "<p>在 QQ 邮箱设置中开启 IMAP/SMTP 服务并生成授权码。"
            "授权码与 QQ 登录密码不同。</p>"
            "<p>此插件供个人使用。邮件内容只在调用时读取，不在服务器中存档。</p>"
        )
        if self.settings.email:
            return page("QQ 邮箱 MCP", text + "邮箱由环境变量配置。")
        extra = (
            '<label for="email">QQ 邮箱地址</label><input id="email" name="email" '
            'type="email" required autocomplete="off">'
            '<label for="code">IMAP/SMTP 授权码</label><input id="code" name="auth_code" '
            'type="password" required autocomplete="off">'
        )
        return page("绑定 QQ 邮箱", text + self.owner_form("/", "验证并保存邮箱", extra))

    async def consent(self, request: Request):
        ticket = request.path_params["ticket"]
        pending = self.store.get("consent", ticket)
        if not pending:
            return page("授权请求已过期", "请回到 ChatGPT 重新发起连接。", 410)
        if request.method == "POST":
            if await self.verified_form(request) is None:
                return page("验证失败", "管理密码不正确或表单已过期，请刷新重试。", 403)
            redirect = self.oauth.approve(ticket)
            if not redirect:
                return page("授权请求已使用", "请重新发起连接。", 410)
            return RedirectResponse(
                redirect,
                status_code=303,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        client = await self.oauth.get_client(pending["client_id"])
        name = html.escape(client.client_name or client.client_id)
        callback = html.escape(pending["params"]["redirect_uri"])
        permissions = "读取邮件"
        if "qq-mail.send" in pending["scopes"]:
            permissions += (
                "，准备邮件并提交你在浏览器批准的草稿"
                if self.settings.require_send_approval
                else "，直接发送邮件（无需逐封网页批准）"
            )
        return page(
            "授权连接",
            f"<p>客户端：{name}</p><p>回调地址：{callback}</p><p>权限：{permissions}</p>"
            "<p>仅当你刚刚发起连接并信任这个客户端时批准。</p>"
            + self.owner_form(request.url.path, "批准连接"),
            form_redirect=pending["params"]["redirect_uri"],
        )

    async def confirm(self, request: Request):
        draft_id = request.path_params["draft_id"]
        draft = self.store.draft(draft_id)
        if not draft:
            return page("草稿不存在或已过期", "请重新准备邮件。", 410)
        if not self.settings.allow_send:
            return page("未启用发送", "请在部署配置中开启发送功能。", 403)
        if request.method == "POST":
            if await self.verified_form(request) is None:
                return page("验证失败", "管理密码不正确或表单已过期，请刷新重试。", 403)
            if not self.store.transition(draft_id, "pending", "approved"):
                return page("草稿状态已变化", "此草稿已批准或已提交，请刷新查看当前状态。", 409)
            return page(
                "草稿已批准",
                "批准仅适用于刚才核对的这份草稿。"
                "<p>回到 ChatGPT，让它提交此草稿发送。不要重新生成草稿。</p>",
            )
        payload = draft["payload"]
        preview = "\n".join(
            [
                "发件人：" + payload["from"],
                "收件人：" + ", ".join(payload["to"]),
                "抄送：" + ", ".join(payload["cc"]),
                "密送：" + ", ".join(payload["bcc"]),
                "主题：" + payload["subject"],
                "",
                payload["body"],
            ]
        )
        content = "<pre>" + html.escape(preview) + "</pre>"
        if draft["state"] == "pending":
            content += "<p>请核对收件人和正文。批准后，该草稿可提交一次，30 分钟后失效。</p>"
            content += self.owner_form(request.url.path, "批准发送这份草稿")
        else:
            content += "<p>状态：" + html.escape(draft["state"]) + "</p>"
        return page("核对待发邮件", content)
