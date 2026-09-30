import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from qq_mail_mcp.auth import READ, SEND
from qq_mail_mcp.config import Settings
from qq_mail_mcp.server import build_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("allow_send", "require_approval", "expected_mode", "has_direct_tool"),
    [
        ("false", "true", "disabled", False),
        ("true", "true", "browser_approval", False),
        ("true", "false", "direct", True),
    ],
)
def test_compose_settings_control_runtime_tools(
    settings, monkeypatch, allow_send, require_approval, expected_mode, has_direct_tool
):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for the distribution integration test")
    env = {
        **os.environ,
        "DOMAIN": "mail.example.com",
        "QQ_MCP_PUBLIC_URL": settings.public_url,
        "QQ_MCP_ADMIN_PASSWORD": settings.admin_password,
        "QQ_MCP_ENCRYPTION_KEY": settings.encryption_key,
        "QQ_MCP_ALLOW_SEND": allow_send,
        "QQ_MCP_REQUIRE_SEND_APPROVAL": require_approval,
        "QQ_EMAIL": "",
        "QQ_AUTH_CODE": "",
    }
    # Render the distributed Compose file: do not bypass it with a Settings fixture.
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", os.devnull, "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(rendered.stdout)
    for name, value in config["services"]["mail"]["environment"].items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("QQ_MCP_DATABASE", str(settings.database))
    runtime = Settings.from_env()
    mcp, store, oauth = build_server(runtime)
    store.bind_mailbox("test@qq.com", "dummy")
    token = oauth.issue("compose-test", [READ, SEND] if runtime.allow_send else [READ])
    headers = {
        "Authorization": f"Bearer {token.access_token}",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(mcp.streamable_http_app(), base_url=runtime.public_url) as client:
        tools = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ).json()["result"]["tools"]
        status = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mailbox_status", "arguments": {}},
            },
        ).json()["result"]["structuredContent"]
    assert status["send_mode"] == expected_mode
    assert ("send_email" in {tool["name"] for tool in tools}) is has_direct_tool
    assert "ports" not in config["services"]["mail"]
