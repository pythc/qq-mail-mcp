"""Container OAuth/PKCE smoke test; no mailbox access or real SMTP calls."""

import base64
import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

origin = "http://localhost:8000"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect())


def request(path, payload=None, token=None, form=False):
    headers = {}
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode()
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    else:
        data = None
    if token:
        headers.update(
            Authorization="Bearer " + token, Accept="application/json, text/event-stream"
        )
    req = urllib.request.Request(origin + path, data=data, headers=headers)
    try:
        response = opener.open(req, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    return response.status, response.headers, response.read()


status, _, raw = request("/.well-known/oauth-authorization-server")
assert status == 200
assert json.loads(raw)["client_id_metadata_document_supported"] is True
status, _, raw = request(
    "/register",
    {
        "redirect_uris": ["http://127.0.0.1:51234/callback"],
        "client_name": "Container CI",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    },
)
assert status == 201
client_id = json.loads(raw)["client_id"]
verifier = secrets.token_urlsafe(48)
challenge = (
    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
)
query = urllib.parse.urlencode(
    {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:51234/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": origin + "/mcp",
        "scope": "qq-mail.read",
        "state": "ci-state",
    }
)
status, headers, _ = request("/authorize?" + query)
assert status == 302
path = urllib.parse.urlsplit(headers["Location"]).path
status, _, raw = request(path)
csrf = re.search(rb'name="csrf" value="([^"]+)"', raw)[1].decode()
status, headers, _ = request(
    path, {"csrf": csrf, "admin_password": os.environ["QQ_MCP_ADMIN_PASSWORD"]}, form=True
)
assert status == 303
values = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["Location"]).query)
assert values["iss"] == [origin] and values["state"] == ["ci-state"]
status, _, raw = request(
    "/token",
    {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": values["code"][0],
        "code_verifier": verifier,
        "redirect_uri": "http://127.0.0.1:51234/callback",
    },
    form=True,
)
assert status == 200
access = json.loads(raw)["access_token"]
status, _, raw = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, access)
assert status == 200
names = {tool["name"] for tool in json.loads(raw)["result"]["tools"]}
assert {"reply_email", "forward_email", "get_send_status", "download_attachment"}.issubset(names)
status, _, _ = request("/revoke", {"client_id": client_id, "token": access}, form=True)
assert status == 200
status, _, _ = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, access)
assert status == 401
print("Container OAuth, PKCE, tools and revocation checks passed; no mail was sent.")
