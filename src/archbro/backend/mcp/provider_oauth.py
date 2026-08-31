from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from archbro.backend.mcp.provider_gateway import ExternalMcpGateway


OAUTH_STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class OAuthProvider:
    id: str
    name: str
    mcp_url: str
    authorization_url: str
    token_url: str
    client_id_env: str
    client_secret_env: str
    scopes: tuple[str, ...]
    scope_separator: str = " "
    use_pkce: bool = False
    tenant_id_env: str | None = None
    connection_kind: str = "mcp"
    omit_client_secret_with_pkce: bool = False


@dataclass
class _PendingOAuth:
    provider_id: str
    redirect_uri: str
    created_at: float
    code_verifier: str | None = None


PROVIDERS: dict[str, OAuthProvider] = {
    "slack": OAuthProvider(
        id="slack",
        name="Slack",
        mcp_url="https://mcp.slack.com/mcp",
        authorization_url="https://slack.com/oauth/v2_user/authorize",
        token_url="https://slack.com/api/oauth.v2.user.access",
        client_id_env="ARCHBRO_SLACK_OAUTH_CLIENT_ID",
        client_secret_env="ARCHBRO_SLACK_OAUTH_CLIENT_SECRET",
        scopes=(
            "search:read.public",
            "search:read.private",
            "search:read.mpim",
            "search:read.im",
            "search:read.files",
            "files:read",
            "search:read.users",
            "channels:history",
            "groups:history",
            "mpim:history",
            "im:history",
            "users:read",
            "users:read.email",
            "channels:read",
            "groups:read",
            "mpim:read",
        ),
        scope_separator=",",
        use_pkce=True,
        omit_client_secret_with_pkce=True,
    ),
    "microsoft-teams": OAuthProvider(
        id="microsoft-teams",
        name="Microsoft Teams",
        # The gateway exposes a local MCP-shaped adapter over Microsoft Graph;
        # this URL is retained as the provider's external API boundary.
        mcp_url="https://graph.microsoft.com/v1.0",
        authorization_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        client_id_env="ARCHBRO_MICROSOFT_TEAMS_CLIENT_ID",
        client_secret_env="ARCHBRO_MICROSOFT_TEAMS_CLIENT_SECRET",
        tenant_id_env="ARCHBRO_MICROSOFT_TEAMS_TENANT_ID",
        scopes=(
            "openid",
            "profile",
            "offline_access",
            "User.Read",
            "Team.ReadBasic.All",
            "Channel.ReadBasic.All",
            "Chat.Read",
            "ChannelMessage.Read.All",
            "ChatMessage.Send",
            "ChannelMessage.Send",
        ),
        use_pkce=True,
        connection_kind="microsoft_teams_graph",
    ),
}


class OAuthSetupRequired(RuntimeError):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__(f"{details['name']} OAuth client is not configured")
        self.details = details


class McpOAuthManager:
    """Memory-only OAuth broker for first-party MCP provider connections."""

    def __init__(self, gateway: ExternalMcpGateway, *, timeout_seconds: float = 15.0) -> None:
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, _PendingOAuth] = {}

    def provider_status(self, provider_id: str, redirect_uri: str) -> dict[str, Any]:
        provider = self._provider(provider_id)
        client_id, client_secret, _ = self._credentials(provider)
        tenant_id = self._tenant_id(provider)
        missing: list[str] = []
        if not client_id:
            missing.append("client ID")
        if not client_secret and not provider.use_pkce:
            missing.append("client secret")
        if provider.tenant_id_env and not tenant_id:
            missing.append("tenant ID")
        return {
            "id": provider.id,
            "name": provider.name,
            "mcp_url": provider.mcp_url,
            "configured": not missing,
            "redirect_uri": redirect_uri,
            "missing_configuration": missing,
        }

    def start(self, provider_id: str, redirect_uri: str) -> str:
        provider = self._provider(provider_id)
        status = self.provider_status(provider_id, redirect_uri)
        if not status["configured"]:
            raise OAuthSetupRequired(status)

        self._prune_pending()
        state = secrets.token_urlsafe(32)
        code_verifier: str | None = None
        client_id, _, _ = self._credentials(provider)
        params: dict[str, str] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        scope_value = provider.scope_separator.join(provider.scopes)
        # Slack's MCP user-token endpoint uses `scope` for user scopes.  The
        # separate `user_scope` parameter belongs to Slack's standard
        # /oauth/v2/authorize installation flow, not /oauth/v2_user/authorize.
        params["scope"] = scope_value
        if provider.id == "google-drive":
            params.update({
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            })
        if provider.use_pkce:
            code_verifier = secrets.token_urlsafe(64)
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        self._pending[state] = _PendingOAuth(
            provider_id=provider.id,
            redirect_uri=redirect_uri,
            created_at=time.time(),
            code_verifier=code_verifier,
        )
        return f"{self._endpoint(provider.authorization_url, provider)}?{urlencode(params)}"

    def complete(
        self,
        provider_id: str,
        *,
        state: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        self._prune_pending()
        pending = self._pending.pop(state, None)
        if pending is None:
            raise ValueError("OAuth state is invalid or expired")
        if pending.provider_id != provider_id:
            raise ValueError("OAuth provider does not match the pending authorization")
        if pending.redirect_uri != redirect_uri:
            raise ValueError("OAuth redirect URI does not match the authorization request")
        provider = self._provider(provider_id)
        status = self.provider_status(provider_id, redirect_uri)
        if not status["configured"]:
            raise OAuthSetupRequired(status)

        client_id, client_secret, _ = self._credentials(provider)
        token_payload: dict[str, str] = {
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        # Slack's public-client flow omits the secret. Microsoft Teams also
        # supports a confidential PKCE deployment, so send its optional secret
        # when one has been provisioned.
        if client_secret and (not provider.use_pkce or not provider.omit_client_secret_with_pkce):
            token_payload["client_secret"] = client_secret
        if pending.code_verifier:
            token_payload["code_verifier"] = pending.code_verifier

        token_url = self._endpoint(provider.token_url, provider)
        token = self._exchange_token(provider, token_payload, token_url=token_url)
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"{provider.name} OAuth response did not include an access token")
        refresh_token = str(token.get("refresh_token") or "").strip() or None
        expires_in = self._coerce_expires_in(token.get("expires_in"))

        if provider.connection_kind == "microsoft_teams_graph":
            connection = self.gateway.add_microsoft_teams_oauth_connection(
                provider=provider.id,
                name=provider.name,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
            )
        else:
            connection = self.gateway.add_oauth_connection(
                provider=provider.id,
                name=provider.name,
                url=provider.mcp_url,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
            )
        if provider.connection_kind == "mcp":
            try:
                verified = self.gateway.probe(connection["id"])
            except (KeyError, RuntimeError, ValueError) as exc:
                self.gateway.remove_connection(connection["id"])
                raise RuntimeError(
                    f"{provider.name} authorization succeeded, but MCP verification failed: {exc}"
                ) from None
            return {"provider": provider.id, "connection": verified["connection"]}
        return {"provider": provider.id, "connection": connection}

    def _exchange_token(
        self,
        provider: OAuthProvider,
        payload: dict[str, str],
        *,
        token_url: str | None = None,
    ) -> dict[str, Any]:
        request = Request(
            token_url or provider.token_url,
            data=urlencode(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ArchBro/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"{provider.name} OAuth token exchange failed with HTTP {exc.code}") from None
        except URLError as exc:
            raise RuntimeError(f"{provider.name} OAuth token exchange failed: {exc.reason}") from None
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"{provider.name} OAuth token exchange returned invalid JSON") from None
        if not isinstance(value, dict):
            raise RuntimeError(f"{provider.name} OAuth token exchange returned an invalid response")
        if provider.id == "slack" and value.get("ok") is False:
            error = str(value.get("error") or "authorization failed")[:200]
            raise RuntimeError(f"Slack OAuth failed: {error}")
        if value.get("error"):
            error = str(value.get("error_description") or value.get("error"))[:200]
            raise RuntimeError(f"{provider.name} OAuth failed: {error}")
        return value

    def _credentials(self, provider: OAuthProvider) -> tuple[str, str, str]:
        client_id = os.getenv(provider.client_id_env, "").strip()
        client_secret = os.getenv(provider.client_secret_env, "").strip()
        return client_id, client_secret, "deployment" if client_id else "none"

    @staticmethod
    def _tenant_id(provider: OAuthProvider) -> str:
        if not provider.tenant_id_env:
            return ""
        return os.getenv(provider.tenant_id_env, "").strip()

    def _endpoint(self, template: str, provider: OAuthProvider) -> str:
        if "{tenant}" not in template:
            return template
        tenant_id = self._tenant_id(provider)
        if not tenant_id:
            raise OAuthSetupRequired(self.provider_status(provider.id, ""))
        return template.replace("{tenant}", quote(tenant_id, safe=""))

    def _prune_pending(self) -> None:
        cutoff = time.time() - OAUTH_STATE_TTL_SECONDS
        stale = [state for state, pending in self._pending.items() if pending.created_at < cutoff]
        for state in stale:
            self._pending.pop(state, None)

    @staticmethod
    def _coerce_expires_in(value: Any) -> int | None:
        if value in {None, ""}:
            return None
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

    @staticmethod
    def _provider(provider_id: str) -> OAuthProvider:
        try:
            return PROVIDERS[provider_id]
        except KeyError:
            raise KeyError(f"unsupported MCP OAuth provider: {provider_id}") from None
