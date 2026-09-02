from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from archbro.backend.mcp.provider_oauth import McpOAuthManager


class FakeGateway:
    def __init__(self) -> None:
        self.added: dict | None = None
        self.removed: list[str] = []

    def add_oauth_connection(self, **kwargs):
        self.added = kwargs
        return {
            "id": "mcp_test",
            "name": kwargs["name"],
            "provider": kwargs["provider"],
            "auth_type": "oauth",
            "endpoint": kwargs["url"],
            "last_probe_ok": None,
        }

    def probe(self, connection_id: str):
        assert connection_id == "mcp_test"
        assert self.added is not None
        return {
            "ok": True,
            "tool_count": 1,
            "connection": {
                "id": connection_id,
                "name": self.added["name"],
                "provider": self.added["provider"],
                "auth_type": "oauth",
                "endpoint": self.added["url"],
                "last_probe_ok": True,
            },
        }

    def remove_connection(self, connection_id: str):
        self.removed.append(connection_id)
        return True


def set_provider_credentials(monkeypatch, provider_id: str) -> tuple[str, str]:
    if provider_id == "github":
        client_id_env = "ARCHBRO_GITHUB_OAUTH_CLIENT_ID"
        client_secret_env = "ARCHBRO_GITHUB_OAUTH_CLIENT_SECRET"
    elif provider_id == "slack":
        client_id_env = "ARCHBRO_SLACK_OAUTH_CLIENT_ID"
        client_secret_env = "ARCHBRO_SLACK_OAUTH_CLIENT_SECRET"
    elif provider_id == "google-drive":
        client_id_env = "ARCHBRO_GOOGLE_DRIVE_OAUTH_CLIENT_ID"
        client_secret_env = "ARCHBRO_GOOGLE_DRIVE_OAUTH_CLIENT_SECRET"
    else:  # pragma: no cover - helper is deliberately narrow
        raise AssertionError(provider_id)
    monkeypatch.setenv(client_id_env, f"{provider_id}-client")
    monkeypatch.setenv(client_secret_env, f"{provider_id}-secret")
    return client_id_env, client_secret_env


@pytest.mark.parametrize(
    ("provider_id", "mcp_url"),
    [
        ("github", "https://api.githubcopilot.com/mcp/"),
        ("slack", "https://mcp.slack.com/mcp"),
        ("google-drive", "https://drivemcp.googleapis.com/mcp/v1"),
    ],
)
def test_public_provider_status_requires_deployment_credentials(monkeypatch, provider_id, mcp_url):
    client_id_env, client_secret_env = set_provider_credentials(monkeypatch, provider_id)
    gateway = FakeGateway()
    manager = McpOAuthManager(gateway)
    redirect_uri = f"https://archbro-dev.magicdala.com/mcp/oauth/{provider_id}/callback"

    monkeypatch.delenv(client_secret_env)
    missing_secret = manager.provider_status(provider_id, redirect_uri)
    assert missing_secret["configured"] is False
    assert "client secret" in missing_secret["missing_configuration"]

    monkeypatch.setenv(client_secret_env, f"{provider_id}-secret")
    configured = manager.provider_status(provider_id, redirect_uri)
    assert configured["configured"] is True
    assert configured["mcp_url"] == mcp_url
    assert configured["redirect_uri"] == redirect_uri
    assert client_id_env not in str(configured)
    assert f"{provider_id}-secret" not in str(configured)


@pytest.mark.parametrize("provider_id", ["github", "slack", "google-drive"])
def test_public_provider_start_uses_public_callback(monkeypatch, provider_id):
    set_provider_credentials(monkeypatch, provider_id)
    manager = McpOAuthManager(FakeGateway())
    redirect_uri = f"https://archbro-dev.magicdala.com/mcp/oauth/{provider_id}/callback"

    authorization_url = manager.start(provider_id, redirect_uri)
    query = parse_qs(urlparse(authorization_url).query)

    assert query["redirect_uri"] == [redirect_uri]
    assert query["response_type"] == ["code"]
    assert query["state"][0]
    assert query["client_id"] == [f"{provider_id}-client"]

    if provider_id in {"github", "google-drive"}:
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"][0]
    else:
        assert "code_challenge_method" not in query
        assert "code_challenge" not in query

    if provider_id == "github":
        scope = set(query["scope"][0].split())
        assert scope == {"repo", "read:org", "read:user"}
        assert query["prompt"] == ["select_account"]
    elif provider_id == "slack":
        scope = set(query["scope"][0].split(","))
        assert {"search:read.public", "files:read", "channels:history"}.issubset(scope)
        # Slack documents that omitting `team` lets the user choose the workspace.
        assert "team" not in query
    else:
        scope = set(query["scope"][0].split())
        assert scope == {"https://www.googleapis.com/auth/drive.readonly"}
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["select_account consent"]


@pytest.mark.parametrize(
    ("provider_id", "remote_mcp_url"),
    [
        ("github", "https://api.githubcopilot.com/mcp/"),
        ("slack", "https://mcp.slack.com/mcp"),
        ("google-drive", "https://drivemcp.googleapis.com/mcp/v1"),
    ],
)
def test_public_provider_callback_builds_backend_only_remote_mcp_connection(
    monkeypatch, provider_id, remote_mcp_url
):
    set_provider_credentials(monkeypatch, provider_id)
    gateway = FakeGateway()
    manager = McpOAuthManager(gateway)
    redirect_uri = f"https://archbro-dev.magicdala.com/mcp/oauth/{provider_id}/callback"
    authorization_url = manager.start(provider_id, redirect_uri)
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    def fake_exchange(provider, payload, *, token_url=None):
        assert payload["redirect_uri"] == redirect_uri
        assert payload["code"] == "authorization-code"
        if provider_id == "slack":
            assert "code_verifier" not in payload
        else:
            assert payload["code_verifier"]
        assert payload["client_secret"] == f"{provider_id}-secret"
        return {
            "access_token": "server-only-access-token",
            "refresh_token": "server-only-refresh-token",
            "expires_in": 3600,
        }

    monkeypatch.setattr(manager, "_exchange_token", fake_exchange)
    result = manager.complete(
        provider_id,
        state=state,
        code="authorization-code",
        redirect_uri=redirect_uri,
    )

    assert gateway.added is not None
    assert gateway.added["url"] == remote_mcp_url
    assert gateway.added["access_token"] == "server-only-access-token"
    assert gateway.added["refresh_token"] == "server-only-refresh-token"
    assert gateway.added["client_secret"] == f"{provider_id}-secret"
    assert result["connection"]["endpoint"] == remote_mcp_url
    assert "server-only-access-token" not in str(result)
    assert "server-only-refresh-token" not in str(result)


def test_frontend_uses_per_user_public_oauth_for_github_slack_and_google_drive():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "web" / "app.js").read_text(encoding="utf-8")
    page = (root / "frontend" / "web" / "index.html").read_text(encoding="utf-8")
    surface = (root / "src" / "archbro" / "backend" / "api" / "provider_connections.py").read_text(
        encoding="utf-8"
    )

    assert "return `/mcp/oauth/${encodeURIComponent(providerId)}/status`;" in app
    assert "await api(`/mcp/oauth/${encodeURIComponent(providerId)}/start`, {method: 'POST'});" in app
    assert "popup.location.replace(started.authorization_url);" in app
    assert '@router.post("/mcp/oauth/{provider_id}/start")' in surface
    assert "mcpLegacyProviderStatusEndpoint" in app
    assert "return '/mcp/auth/github/status';" in app
    assert "return '/mcp/auth/google-drive/status';" in app
    assert "oauth_strategy: 'legacy-runtime'" in app
    assert "await api(`/mcp/auth/${encodeURIComponent(providerId)}/start`, {method: 'POST'});" in app
    assert "https://mcp.slack.com/mcp" in app
    assert "https://drivemcp.googleapis.com/mcp/v1" in app
    assert "Sign in to your own GitHub account" in app
    assert "Sign in to your own Slack workspace account" in app
    assert "Sign in to your own Google account" in app
    assert "GitHub OAuth runtime unavailable" not in page
    assert "Google Drive prerequisite" not in page
    assert "ARCHBRO_OAUTH_REDIRECT_BASE_URL" in surface


def test_provider_oauth_state_and_runtimes_are_scoped_by_trusted_principal():
    root = Path(__file__).resolve().parents[1]
    surface = (root / "src" / "archbro" / "backend" / "api" / "provider_connections.py").read_text(
        encoding="utf-8"
    )

    assert "user_id = principal.user_id" in surface
    assert "gateways[user_id] = gateway" in surface
    assert "oauth_managers[user_id] = McpOAuthManager(gateway)" in surface
    assert "oauth_state_owners[parsed_state[0]] = (principal.user_id, started_at)" in surface
    assert "prune_oauth_transient_state()" in surface
    assert "owner_record = oauth_state_owners.pop(state, None) if state else None" in surface
    assert "OAUTH_STATE_TTL_SECONDS = 600" in surface
    assert "OAUTH_MAX_PENDING_PER_USER = 8" in surface
    assert "OAUTH_MAX_STARTS_PER_WINDOW = 6" in surface
    assert "runtime = runtime_for_owner(owner_user_id)" in surface
