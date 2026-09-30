"""Public HTTPS-only CIMD retrieval, with pinned DNS and bounded JSON responses."""

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull


class MetadataClient(OAuthClientInformationFull):
    def validate_redirect_uri(self, redirect_uri):
        try:
            return super().validate_redirect_uri(redirect_uri)
        except InvalidRedirectUriError:
            # RFC 8252 permits a runtime port for native loopback callbacks.
            if redirect_uri:
                actual = urlsplit(str(redirect_uri))
                for uri in self.redirect_uris or []:
                    expected = urlsplit(str(uri))
                    if (
                        actual.scheme == expected.scheme == "http"
                        and actual.hostname == expected.hostname
                        and actual.hostname in ("127.0.0.1", "::1", "localhost")
                        and actual.path == expected.path
                        and actual.query == expected.query
                        and not actual.fragment
                        and not actual.username
                        and not actual.password
                    ):
                        return redirect_uri
            raise


def public_address(value):
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address):
        super().__init__(hostname, port=443, timeout=8, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch_metadata(client_id):
    url = urlsplit(client_id)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.path in ("", "/")
        or url.username
        or url.password
        or url.fragment
        or url.port not in (None, 443)
        or len(client_id) > 2048
    ):
        raise ValueError("Invalid CIMD URL")
    addresses = {
        entry[4][0] for entry in socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)
    }
    if not addresses or not all(public_address(ip) for ip in addresses):
        raise ValueError("CIMD requires public addresses")
    conn = PinnedHTTPS(url.hostname, sorted(addresses)[0])
    try:
        conn.request(
            "GET",
            url.path + ("?" + url.query if url.query else ""),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )
        response = conn.getresponse()
        if (
            response.status != 200
            or response.getheader("Content-Type", "").split(";", 1)[0] != "application/json"
        ):
            raise ValueError("CIMD must return JSON without redirects")
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError("CIMD too large")
        value = json.loads(data)
        if (
            not isinstance(value, dict)
            or value.get("client_id") != client_id
            or not isinstance(value.get("client_name"), str)
            or not value["client_name"]
            or not isinstance(value.get("redirect_uris"), list)
            or not value["redirect_uris"]
            or len(value["redirect_uris"]) > 20
            or value.get("token_endpoint_auth_method", "none") != "none"
        ):
            raise ValueError("Invalid CIMD metadata")
        # Public clients only. Never accept document-supplied client secrets.
        value["token_endpoint_auth_method"] = "none"
        value.pop("client_secret", None)
        cache = response.getheader("Cache-Control", "")
        import re

        match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache)
        ttl = min(int(match[1]), 3600) if match else 300
        if "no-store" in cache or "no-cache" in cache:
            ttl = 0
        return value, ttl
    finally:
        conn.close()
