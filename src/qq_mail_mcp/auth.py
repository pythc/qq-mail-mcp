import secrets
import time
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

READ = "qq-mail.read"
SEND = "qq-mail.send"


class PersonalOAuth:
    """OAuth authorization-code + PKCE provider for one mailbox owner.

    The MCP SDK validates redirect URIs, client authentication and S256 PKCE.
    Owner login/consent happens on this server, never inside an MCP tool.
    """

    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.resource = settings.public_url + "/mcp"
        self.scopes = [READ, SEND] if settings.allow_send else [READ]

    async def get_client(self, client_id):
        value = self.store.get("client", client_id)
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def register_client(self, client_info):
        for uri in client_info.redirect_uris:
            url = urlsplit(str(uri))
            if (
                url.fragment
                or url.username
                or url.password
                or (
                    url.scheme != "https"
                    and not (
                        url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
                    )
                )
            ):
                raise RegistrationError("invalid_redirect_uri", "HTTPS redirects required")
        self.store.put(
            "client", client_info.client_id, client_info.model_dump(mode="json"), ttl=365 * 86400
        )

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_target", "Unexpected resource")
        scopes = params.scopes if params.scopes is not None else self.scopes
        if not scopes or not set(scopes).issubset(self.scopes):
            raise AuthorizeError("invalid_scope", "Unsupported scope")
        ticket = secrets.token_urlsafe(32)
        self.store.put(
            "consent",
            ticket,
            {
                "client_id": client.client_id,
                "params": params.model_dump(mode="json"),
                "scopes": scopes,
            },
            ttl=600,
        )
        return self.settings.public_url + "/consent/" + ticket

    def approve(self, ticket):
        pending = self.store.get("consent", ticket, consume=True)
        if not pending:
            return None
        params = AuthorizationParams.model_validate(pending["params"])
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code=code,
            scopes=pending["scopes"],
            expires_at=time.time() + 120,
            client_id=pending["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject="owner",
        )
        self.store.put("code", code, record.model_dump(mode="json", exclude={"code"}), ttl=120)
        query = {"code": code}
        if params.state is not None:
            query["state"] = params.state
        uri = str(params.redirect_uri)
        return uri + ("&" if "?" in uri else "?") + urlencode(query)

    async def load_authorization_code(self, client, authorization_code):
        value = self.store.get("code", authorization_code)
        if not value or value["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **value)

    async def exchange_authorization_code(self, client, authorization_code):
        value = self.store.get("code", authorization_code.code, consume=True)
        if not value or value["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Code expired or already exchanged")
        return self.issue(client.client_id, value["scopes"])

    def issue(self, client_id, scopes):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        common = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": self.resource,
            "subject": "owner",
        }
        self.store.put(
            "access", access, {**common, "expires_at": now + 3600, "pair": refresh}, ttl=3600
        )
        self.store.put(
            "refresh",
            refresh,
            {**common, "expires_at": now + 7 * 86400, "pair": access},
            ttl=7 * 86400,
        )
        return OAuthToken(
            access_token=access, refresh_token=refresh, expires_in=3600, scope=" ".join(scopes)
        )

    async def load_refresh_token(self, client, refresh_token):
        value = self.store.get("refresh", refresh_token)
        if not value or value["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, **value)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        value = self.store.get("refresh", refresh_token.token, consume=True)
        if not value or value["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Refresh token expired or already used")
        self.store.delete("access", value["pair"])
        if not set(scopes).issubset(value["scopes"]):
            raise TokenError("invalid_scope", "Cannot expand scopes")
        return self.issue(client.client_id, scopes)

    async def load_access_token(self, token):
        value = self.store.get("access", token)
        return AccessToken(token=token, **value) if value else None

    async def revoke_token(self, token):
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        value = self.store.get(kind, token.token, consume=True)
        if value:
            self.store.delete("refresh" if kind == "access" else "access", value["pair"])
