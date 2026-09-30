import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import AnyUrl
from starlette.testclient import TestClient
from test_http import csrf, rpc

from qq_mail_mcp.cimd import MetadataClient, fetch_metadata, public_address

CLIENT = "https://agent.example.com/client.json"


def test_cimd_end_to_end_pkce_loopback_port_and_issuer(server, settings, monkeypatch):
    def fetch(url):
        assert url == CLIENT
        return {
            "client_id": url,
            "client_name": "My native agent",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        }, 300

    monkeypatch.setattr("qq_mail_mcp.auth.fetch_metadata", fetch)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    with TestClient(server[3], base_url=settings.public_url) as client:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["client_id_metadata_document_supported"] is True
        assert metadata["authorization_response_iss_parameter_supported"] is True
        response = client.get(
            "/authorize",
            params={
                "client_id": CLIENT,
                "response_type": "code",
                "redirect_uri": "http://127.0.0.1:51234/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "qq-mail.read",
                "resource": settings.public_url + "/mcp",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        path = urlsplit(response.headers["location"]).path
        form = client.get(path)
        assert "My native agent" in form.text
        consent = client.post(
            path,
            data={"csrf": csrf(form), "admin_password": settings.admin_password},
            follow_redirects=False,
        )
        values = parse_qs(urlsplit(consent.headers["location"]).query)
        assert values["iss"] == [settings.public_url]
        issued = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT,
                "code": values["code"][0],
                "code_verifier": verifier,
                "redirect_uri": "http://127.0.0.1:51234/callback",
            },
        )
        assert issued.status_code == 200, issued.text
        token = issued.json()["access_token"]
        assert "result" in rpc(client, token, "tools/list")
        revoked = client.post("/revoke", data={"client_id": CLIENT, "token": token})
        assert revoked.status_code == 200
        assert server[1].get("access", token) is None


@pytest.mark.parametrize(
    "actual",
    [
        "http://127.0.0.2:123/callback",
        "http://127.0.0.1:123/other",
        "http://127.0.0.1:123/callback?bad=1",
        "https://evil.example/callback",
    ],
)
def test_loopback_does_not_relax_host_path_or_query(actual):
    from mcp.shared.auth import InvalidRedirectUriError

    client = MetadataClient(client_id=CLIENT, redirect_uris=[AnyUrl("http://127.0.0.1/callback")])
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl(actual))


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "fc00::1", "192.0.2.1"],
)
def test_private_and_reserved_metadata_addresses_rejected(ip):
    assert public_address(ip) is False


def test_metadata_retrieval_is_pinned_bounded_and_rejects_redirects(monkeypatch):
    state = {
        "status": 200,
        "body": json.dumps(
            {
                "client_id": CLIENT,
                "client_name": "Native client",
                "redirect_uris": ["https://example.com/callback"],
            }
        ).encode(),
        "ip": "93.184.216.34",
    }
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (state["ip"], 443))])

    class HTTP:
        def __init__(self, host, ip):
            assert host == "agent.example.com" and ip == state["ip"]

        def request(self, method, path, headers):
            assert method == "GET" and path == "/client.json"
            assert headers["Accept-Encoding"] == "identity"

        def getresponse(self):
            return self

        @property
        def status(self):
            return state["status"]

        def getheader(self, name, default=""):
            return "application/json" if name == "Content-Type" else "max-age=120"

        def read(self, limit):
            return state["body"][:limit]

        def close(self):
            pass

    monkeypatch.setattr("qq_mail_mcp.cimd.PinnedHTTPS", HTTP)
    value, ttl = fetch_metadata(CLIENT)
    assert value["client_id"] == CLIENT and ttl == 120
    state["status"] = 302
    with pytest.raises(ValueError):
        fetch_metadata(CLIENT)
    state.update(status=200, body=b"x" * 65537)
    with pytest.raises(ValueError):
        fetch_metadata(CLIENT)
    state["ip"] = "169.254.169.254"
    with pytest.raises(ValueError):
        fetch_metadata(CLIENT)


def test_auth_rate_limit_does_not_block_health(server, settings):
    with TestClient(server[3], base_url=settings.public_url) as client:
        for _ in range(60):
            assert client.get("/manage").status_code == 200
        response = client.get("/manage")
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        assert client.get("/healthz").status_code == 200
