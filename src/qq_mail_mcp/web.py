import asyncio
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from .connect import connection_content, connection_download
from .mail import Mailbox, MailError, account, draft_preview


def page(title, content, status=200, form_redirect=None, lang="zh"):
    lang = "en" if lang == "en" else "zh"
    nonce = secrets.token_urlsafe(16)
    form_sources = "'self'"
    if form_redirect:
        # Browser form-action also governs the POST's OAuth 303 redirect.
        # This URI has already passed registration and exact-URI validation.
        callback = urlsplit(form_redirect)
        # Encode CSP delimiters and wildcards instead of interpolating them.
        form_sources += " " + callback.scheme + "://" + quote(callback.netloc, safe="[]:.")
    return HTMLResponse(
        f"""<!doctype html><html lang="{"en" if lang == "en" else "zh-CN"}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ Mail MCP</title><style>
:root{{color-scheme:light;--ink:#172440;--muted:#64748b;--blue:#3455dd}}
*{{box-sizing:border-box}}
body{{font:15px/1.6 system-ui,sans-serif;
background:#f4f6fb;
color:var(--ink);
margin:0}}

main{{max-width:980px;
margin:5vh auto;
padding:36px;
background:white;
border:1px solid #e7ebf4;
border-radius:24px;
box-shadow:0 16px 50px #22345608}}

nav{{display:flex;
gap:18px;
align-items:center;
padding-bottom:24px;
margin-bottom:28px;
border-bottom:1px solid #edf0f6}}
nav strong{{margin-right:auto}}

h1{{font-size:30px;
letter-spacing:-.03em;
margin:0 0 18px}}
h2{{font-size:19px;
margin:0 0 14px}}
label{{display:block;
margin:18px 0 6px}}

input,button{{font:inherit;
width:100%;
padding:12px 14px;
border:1px solid #d7deea;
border-radius:10px}}

input:focus-visible{{outline:2px solid #a0b3ff;
outline-offset:2px}}
button,.button{{background:var(--blue);
color:white;
border:0;
margin-top:18px;
cursor:pointer}}

.button{{display:inline-block;
padding:10px 16px;
text-decoration:none;
border-radius:10px}}
button.secondary{{background:#f1f4fb;
color:var(--ink);
width:auto;
margin-top:0}}

pre{{white-space:pre-wrap;
overflow-wrap:anywhere;
background:#f5f7fc;
padding:16px;
border-radius:10px;
font-size:13px}}
a{{color:var(--blue);
text-underline-offset:3px}}

small,.muted{{color:var(--muted)}}
.client-grid{{display:grid;
grid-template-columns:1fr 1fr;
gap:16px}}
.client-card{{padding:24px;
border:1px solid #e3e9f4;
border-radius:18px;
min-width:0}}

form{{max-width:620px}}
.status{{display:inline-block;
background:#e8f6ef;
color:#19704b;
border-radius:99px;
padding:5px 12px}}
.row{{border-top:1px solid #edf0f6;
padding:18px 0}}

@media(max-width:700px){{main{{margin:12px;
padding:22px}}
.client-grid{{grid-template-columns:1fr}}
nav{{flex-wrap:wrap;
gap:12px}}
}}

@media(prefers-reduced-motion:no-preference){{main{{animation:enter .35s ease-out}}
button:hover,.button:hover{{filter:brightness(1.08)}}
@keyframes enter{{from{{opacity:0;
transform:translateY(8px)}}
}}
}}

</style><main><nav><strong>QQ Mail MCP</strong>
<a href="/?lang={lang}">{"Home" if lang == "en" else "首页"}</a>
<a href="/connect?lang={lang}">{"Connect" if lang == "en" else "连接客户端"}</a>
<a href="/manage?lang={lang}">{"Manage" if lang == "en" else "管理"}</a>
<a href="?lang={"zh" if lang == "en" else "en"}">{"中文" if lang == "en" else "English"}</a>
</nav><h1>"""
        + html.escape(title)
        + "</h1>"
        + content
        + f"""</main><script nonce="{nonce}">document.querySelectorAll('.copy').forEach(button=>
button.addEventListener('click',async()=>{{try{{
await navigator.clipboard.writeText(button.previousElementSibling.textContent);
button.textContent={'"Copied"' if lang == "en" else '"已复制"'};
}}catch{{button.previousElementSibling.setAttribute('tabindex','0');
button.previousElementSibling.focus();
button.textContent={'"Select and copy the text"' if lang == "en" else '"请选择文字复制"'};
}}}}));
</script></html>""",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            # no-referrer makes native browser POST forms send Origin: null.
            # Preserve same-origin checks without leaking consent URLs off-site.
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; "
            "style-src 'unsafe-inline'; "
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
        form = await request.form(max_fields=10, max_files=0)
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
        if request.url.path.startswith("/manage/") and self.session(request):
            return form
        supplied = str(form.get("admin_password", ""))
        if not hmac.compare_digest(supplied.encode(), self.settings.admin_password.encode()):
            # A deployment must also rate-limit this route at its reverse proxy.
            await asyncio.sleep(1)
            return None
        return form

    def owner_form(self, path, label, extra=""):
        password_label = "插件管理密码" if "lang=zh" in path else "Service admin password"
        return (
            f'<form method="post" action="{html.escape(path, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{self.csrf(urlsplit(path).path)}">'
            + extra
            + f'<label for="password">{password_label}</label>'
            '<input id="password" type="password" name="admin_password" '
            'required autocomplete="current-password">'
            f'<button type="submit">{html.escape(label)}</button></form>'
        )

    def language(self, request):
        return "zh" if request.query_params.get("lang") == "zh" else "en"

    def session(self, request):
        token = request.cookies.get("qq_mail_owner", "")
        return bool(token and self.store.get("owner_session", token))

    async def connections(self, request):
        lang = self.language(request)
        return page(
            "连接客户端" if lang == "zh" else "Connect your agent",
            connection_content(self.settings.public_url, lang),
            lang=lang,
        )

    async def download(self, request):
        return await connection_download(request, self.settings.public_url)

    async def setup(self, request: Request):
        lang = self.language(request)
        zh = lang == "zh"
        if request.method == "POST":
            form = await self.verified_form(request)
            if form is None:
                return page(
                    "验证失败" if zh else "Verification failed",
                    "Incorrect password or expired form.",
                    403,
                    lang=lang,
                )
            if self.settings.email:
                return page(
                    "Managed through environment",
                    "Update the deployment configuration.",
                    409,
                    lang=lang,
                )
            try:
                email, code = account(str(form.get("email", "")), str(form.get("auth_code", "")))
                await asyncio.to_thread(Mailbox(email, code).check)
                self.store.bind_mailbox(email, code)
            except MailError as exc:
                return page(
                    "绑定失败" if zh else "Binding failed", html.escape(str(exc)), 400, lang=lang
                )
            return page(
                "邮箱已绑定" if zh else "Mailbox connected",
                f'<p><a class="button" href="/connect?lang={lang}">'
                + ("连接你的 Agent" if zh else "Connect your agent")
                + "</a></p>",
                lang=lang,
            )
        bound = bool(self.settings.email or self.store.get("mailbox", "owner"))
        if bound:
            content = (
                '<span class="status">' + ("邮箱已配置" if zh else "Mailbox configured") + "</span>"
            )
            content += (
                '<p class="muted">'
                + ("选择客户端并完成授权。" if zh else "Choose a client and authorize access.")
                + "</p>"
            )
            content += (
                f'<a class="button" href="/connect?lang={lang}">'
                + ("连接 Agent" if zh else "Connect an agent")
                + "</a>"
            )
            content += (
                f' <a href="/manage?lang={lang}">'
                + ("管理邮箱" if zh else "Manage mailbox")
                + "</a>"
            )
            return page("QQ Mail MCP", content, lang=lang)
        extra = (
            '<label for="email">'
            + ("QQ 邮箱地址" if zh else "QQ mailbox address")
            + '</label><input id="email" name="email" type="email" required autocomplete="email">'
            '<label for="code">'
            + ("IMAP/SMTP 授权码" if zh else "IMAP/SMTP authorization code")
            + '</label><input id="code" name="auth_code" type="password" '
            'required autocomplete="off">'
        )
        content = (
            '<p class="muted">'
            + (
                "在 QQ 邮箱设置中开启 IMAP/SMTP，使用生成的授权码。"
                if zh
                else "Enable IMAP/SMTP in QQ Mail settings. Use the generated authorization code."
            )
            + "</p>"
        )
        content += self.owner_form(
            "/?lang=" + lang, "验证并保存" if zh else "Verify and save", extra
        )
        return page("绑定邮箱" if zh else "Connect your mailbox", content, lang=lang)

    async def manage(self, request):
        lang = self.language(request)
        zh = lang == "zh"
        if request.method == "POST":
            if await self.verified_form(request) is None:
                return page(
                    "验证失败" if zh else "Verification failed",
                    "Incorrect password or expired form.",
                    403,
                    lang=lang,
                )
            token = secrets.token_urlsafe(32)
            self.store.put("owner_session", token, {"owner": True}, ttl=3600)
            response = RedirectResponse("/manage?lang=" + lang, status_code=303)
            response.set_cookie(
                "qq_mail_owner",
                token,
                max_age=3600,
                httponly=True,
                secure=self.settings.public_url.startswith("https:"),
                samesite="strict",
                path="/manage",
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        if not self.session(request):
            return page(
                "管理员登录" if zh else "Owner sign in",
                self.owner_form("/manage?lang=" + lang, "登录" if zh else "Sign in"),
                lang=lang,
            )
        try:
            email = self.get_mailbox().email
        except MailError:
            email = "—"
        mode = (
            "disabled"
            if not self.settings.allow_send
            else "approval"
            if self.settings.require_send_approval
            else "direct"
        )
        content = (
            "<p><strong>"
            + html.escape(email)
            + "</strong></p><p>"
            + ("发送模式：" if zh else "Send mode: ")
            + mode
            + "</p>"
        )
        content += (
            '<form method="post" action="/manage/check"><input type="hidden" name="csrf" value="'
            + self.csrf("/manage/check")
            + '"><button class="secondary">'
            + ("检查邮箱连接" if zh else "Check IMAP connection")
            + "</button></form>"
        )
        content += "<h2>" + ("已授权客户端" if zh else "Authorized clients") + "</h2>"
        for grant in self.store.grants():
            client_id = grant["client_id"]
            name = grant.get("client_name", client_id)
            content += (
                '<div class="row"><strong>'
                + html.escape(name or client_id)
                + '</strong><p class="muted">'
                + html.escape(client_id)
                + "</p>"
            )
            content += (
                '<form method="post" action="/manage/revoke">'
                '<input type="hidden" name="csrf" value="'
                + self.csrf("/manage/revoke")
                + '"><input type="hidden" name="client_id" value="'
                + html.escape(client_id, quote=True)
                + '"><button class="secondary">'
                + ("撤销授权" if zh else "Revoke access")
                + "</button></form></div>"
            )
        content += "<h2>" + ("近期发送记录" if zh else "Recent outgoing messages") + "</h2>"
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload, state FROM drafts WHERE expires>? ORDER BY expires DESC LIMIT 20",
                (time.time(),),
            ).fetchall()
        for row in rows:
            payload = self.store.unpack(row["payload"])
            content += (
                '<div class="row"><strong>'
                + html.escape(payload.get("subject", ""))
                + '</strong><p class="muted">'
                + html.escape(row["state"])
                + " · "
                + html.escape(", ".join(payload.get("to", [])))
                + "</p></div>"
            )
        content += (
            '<form method="post" action="/manage/logout"><input type="hidden" name="csrf" value="'
            + self.csrf("/manage/logout")
            + '"><button class="secondary">'
            + ("退出" if zh else "Sign out")
            + "</button></form>"
        )
        return page("邮箱管理" if zh else "Mailbox settings", content, lang=lang)

    async def manage_action(self, request):
        lang = self.language(request)
        if not self.session(request) or await self.verified_form(request) is None:
            return page("Verification failed", "Sign in again.", 403, lang=lang)
        action = request.path_params["action"]
        if action == "revoke":
            form = await request.form()
            self.store.revoke_client(str(form.get("client_id", "")))
        elif action == "check":
            try:
                await asyncio.to_thread(self.get_mailbox().check)
            except MailError as exc:
                return page("Connection failed", html.escape(str(exc)), 502, lang=lang)
            return page("IMAP connected", '<a href="/manage">Back</a>', lang=lang)
        elif action != "logout":
            return page("Not found", "", 404, lang=lang)
        response = RedirectResponse("/manage?lang=" + lang, status_code=303)
        if action == "logout":
            self.store.delete("owner_session", request.cookies.get("qq_mail_owner", ""))
            response.delete_cookie("qq_mail_owner", path="/manage")
        response.headers["Cache-Control"] = "no-store"
        return response

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
        if client is None:
            return page("Client unavailable", "Retry the connection from your client.", 503)
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
        files = draft_preview(payload).get("attachments", [])
        if files:
            preview += "\n\nAttachments:\n" + "\n".join(
                f"{file['name']} · {file['size']} bytes · SHA256 {file['sha256']}" for file in files
            )
        content = "<pre>" + html.escape(preview) + "</pre>"
        if draft["state"] == "pending":
            content += "<p>请核对收件人和正文。批准后，该草稿可提交一次，30 分钟后失效。</p>"
            content += self.owner_form(request.url.path, "批准发送这份草稿")
        else:
            content += "<p>状态：" + html.escape(draft["state"]) + "</p>"
        return page("核对待发邮件", content)
