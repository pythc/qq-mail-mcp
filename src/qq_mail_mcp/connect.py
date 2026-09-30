"""Credential-free client recipes. Configuration installation never grants mail access."""

import base64
import html
import json
import shlex
from urllib.parse import quote, urlencode, urlsplit

from starlette.responses import JSONResponse, Response

NAME = "qq-mail"
CLIENTS = ("cursor", "vscode", "codex", "claude-code", "windsurf", "chatgpt")


def endpoint(value):
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("URL must not contain whitespace or control characters")
    url = urlsplit(value)
    if (
        url.scheme not in ("https", "http")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/", "/mcp")
    ):
        raise ValueError(
            "Use the HTTPS origin or /mcp URL, without credentials or query parameters"
        )
    if url.scheme != "https" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Remote MCP connections require HTTPS")
    return value.rstrip("/") if url.path == "/mcp" else value.rstrip("/") + "/mcp"


def recipes(value):
    url = endpoint(value)
    cursor_config = {"url": url}
    cursor_link = "cursor://anysphere.cursor-deeplink/mcp/install?" + urlencode(
        {
            "name": NAME,
            "config": base64.b64encode(
                json.dumps(cursor_config, separators=(",", ":")).encode()
            ).decode(),
        }
    )
    vscode_config = {"name": NAME, "type": "http", "url": url}
    vscode_link = "vscode:mcp/install?" + quote(
        json.dumps(vscode_config, separators=(",", ":")), safe=""
    )
    return {
        "cursor": {
            "label": "Cursor",
            "install_url": cursor_link,
            "config": {"mcpServers": {NAME: cursor_config}},
            "next": "Complete OAuth in Cursor.",
        },
        "vscode": {
            "label": "VS Code / Copilot",
            "install_url": vscode_link,
            "config": {"servers": {NAME: {"type": "http", "url": url}}},
            "next": "Start the server and complete OAuth in VS Code.",
        },
        "codex": {
            "label": "Codex",
            "command": f"codex mcp add {NAME} --url {shlex.quote(url)}\ncodex mcp login {NAME}",
            "config": f"[mcp_servers.{NAME}]\nurl = {json.dumps(url)}\n",
            "next": "The login command opens OAuth in your browser.",
        },
        "claude-code": {
            "label": "Claude Code",
            "command": f"claude mcp add --transport http --scope user {NAME} {shlex.quote(url)}",
            "config": {"mcpServers": {NAME: {"type": "http", "url": url}}},
            "next": "Run /mcp in Claude Code and authenticate.",
        },
        "windsurf": {
            "label": "Windsurf / Cascade",
            "config": {"mcpServers": {NAME: {"serverUrl": url}}},
            "next": "Merge into your client's MCP configuration, refresh, and complete OAuth.",
        },
        "chatgpt": {
            "label": "ChatGPT",
            "url": url,
            "next": (
                "Add this URL as an OAuth connection in your available "
                "developer-mode / custom-app settings."
            ),
        },
    }


def connection_content(public_url, lang="en"):
    zh = lang == "zh"
    entries = recipes(public_url)
    output = (
        '<p class="muted">'
        + (
            "选择客户端，安装配置后完成邮箱授权。"
            if zh
            else "Choose your client, then authorize mailbox access."
        )
        + '</p><div class="client-grid">'
    )
    for client, entry in entries.items():
        output += '<section class="client-card"><h2>' + entry["label"] + "</h2>"
        if entry.get("install_url"):
            output += (
                '<a class="button" href="'
                + html.escape(entry["install_url"], quote=True)
                + '">'
                + ("添加到 " if zh else "Add to ")
                + entry["label"]
                + "</a>"
            )
        text = entry.get("command") or entry.get("url") or json.dumps(entry["config"], indent=2)
        output += (
            "<pre>"
            + html.escape(text)
            + '</pre><button class="secondary copy" type="button">'
            + ("复制" if zh else "Copy")
            + "</button>"
        )
        hints = {
            "cursor": "在 Cursor 完成 OAuth 授权。",
            "vscode": "启动 MCP 服务并完成 OAuth 授权。",
            "codex": "login 命令会打开浏览器授权页。",
            "claude-code": "在 Claude Code 输入 /mcp，完成授权。",
            "windsurf": "合并配置并刷新，在客户端完成授权。",
            "chatgpt": "在可用的开发者模式或自定义应用设置中添加此地址，选择 OAuth。",
        }
        output += '<p class="muted">' + html.escape(hints[client] if zh else entry["next"]) + "</p>"
        if "config" in entry:
            output += (
                f'<a href="/connect/{client}?lang={lang}">'
                + ("下载配置" if zh else "Download configuration")
                + "</a>"
            )
        output += "</section>"
    return output + "</div>"


async def connection_download(request, public_url):
    client = request.path_params["client"]
    entry = recipes(public_url).get(client)
    if not entry or "config" not in entry:
        return JSONResponse({"error": "Unknown configuration"}, status_code=404)
    data = entry["config"]
    return Response(
        data if isinstance(data, str) else json.dumps(data, indent=2) + "\n",
        media_type="text/plain" if isinstance(data, str) else "application/json",
        headers={
            "Content-Disposition": f'attachment; filename="qq-mail-{client}.'
            + ("toml" if isinstance(data, str) else "json")
            + '"',
            "Cache-Control": "no-store",
        },
    )
