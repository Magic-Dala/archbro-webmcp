from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.mcp.provider_gateway import McpConnectionConfig
from archbro.backend.mcp.provider_oauth import McpOAuthManager
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database

pytestmark = requires_database


def _client(dsn, tmp_path, monkeypatch, *, environment="test", base_url="http://127.0.0.1:8012") -> TestClient:
    async def principal_provider(token: str) -> TrustedPrincipal:
        if token in {"local-a", "local-b"}:
            return TrustedPrincipal(user_id=token, local_development=True)
        if token == "prod-user":
            return TrustedPrincipal(user_id=token)
        raise ValueError("unknown test token")

    monkeypatch.setenv("ARCHBRO_ENV", environment)
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "local")
    repo = PostgresProjectRepository(dsn)
    return TestClient(build_app(repo, FakeModelProvider(), principal_provider=principal_provider), base_url=base_url)


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user}"}


def test_dynamic_provider_connections_are_principal_scoped(dsn, tmp_path, monkeypatch):
    client = _client(dsn, tmp_path, monkeypatch)
    created = client.post(
        "/mcp/connections",
        headers=_auth("local-a"),
        json={
            "name": "Local test MCP",
            "transport": "streamable_http",
            "url": "http://127.0.0.1:65530/mcp",
        },
    )
    assert created.status_code == 200
    connection_id = created.json()["id"]

    owner_connections = client.get("/mcp/connections", headers=_auth("local-a"))
    other_connections = client.get("/mcp/connections", headers=_auth("local-b"))

    assert [item["id"] for item in owner_connections.json()] == [connection_id]
    assert other_connections.json() == []
    assert client.delete(f"/mcp/connections/{connection_id}", headers=_auth("local-b")).status_code == 404
    assert client.delete(f"/mcp/connections/{connection_id}", headers=_auth("local-a")).status_code == 204


def test_tunneled_local_principal_cannot_access_provider_hub_or_register_stdio_command(
    dsn, tmp_path, monkeypatch
):
    client = _client(
        dsn,
        tmp_path,
        monkeypatch,
        environment="local",
        base_url="https://archbro-webmcp.magicdala.com",
    )
    created = client.post(
        "/mcp/connections",
        headers=_auth("local-a"),
        json={
            "name": "Blocked command MCP",
            "transport": "stdio",
            "command": "python",
            "args": ["-c", "print('must not execute')"],
        },
    )
    assert created.status_code == 403
    assert "verified per-user authentication" in created.json()["detail"]

    listed = client.get("/mcp/connections", headers=_auth("local-a"))
    assert listed.status_code == 403
    oauth_status = client.get("/mcp/oauth/slack/status", headers=_auth("local-a"))
    assert oauth_status.status_code == 403
    assert "Firebase authentication" in oauth_status.json()["detail"]


def test_custom_browser_supplied_mcp_is_disabled_for_non_local_principals(dsn, tmp_path, monkeypatch):
    client = _client(dsn, tmp_path, monkeypatch)
    response = client.post(
        "/mcp/connections",
        headers=_auth("prod-user"),
        json={
            "name": "Unsafe custom",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
        },
    )
    assert response.status_code == 403
    assert "deployment-configured" in response.json()["detail"]


@pytest.mark.parametrize(
    ("provider_id", "provider_name", "mcp_url", "client_id_env", "client_secret_env"),
    [
        ("github", "GitHub", "https://api.githubcopilot.com/mcp/", "ARCHBRO_GITHUB_OAUTH_CLIENT_ID", "ARCHBRO_GITHUB_OAUTH_CLIENT_SECRET"),
        ("slack", "Slack", "https://mcp.slack.com/mcp", "ARCHBRO_SLACK_OAUTH_CLIENT_ID", "ARCHBRO_SLACK_OAUTH_CLIENT_SECRET"),
        ("google-drive", "Google Drive", "https://drivemcp.googleapis.com/mcp/v1", "ARCHBRO_GOOGLE_DRIVE_OAUTH_CLIENT_ID", "ARCHBRO_GOOGLE_DRIVE_OAUTH_CLIENT_SECRET"),
    ],
)
def test_oauth_callback_state_routes_back_to_the_starting_principal(
    dsn, tmp_path, monkeypatch, provider_id, provider_name, mcp_url, client_id_env, client_secret_env
):
    monkeypatch.setenv(client_id_env, f"{provider_id}-client")
    monkeypatch.setenv(client_secret_env, f"{provider_id}-secret")

    def fake_complete(self, provider_id, *, state, code, redirect_uri):
        connection = self.gateway.add_connection(
            McpConnectionConfig(
                name=provider_name,
                transport="streamable_http",
                url=mcp_url,
            )
        )
        return {"provider": provider_id, "connection": connection}

    monkeypatch.setattr(McpOAuthManager, "complete", fake_complete)
    client = _client(dsn, tmp_path, monkeypatch)
    started = client.get(
        f"/mcp/oauth/{provider_id}/start",
        headers=_auth("local-a"),
        follow_redirects=False,
    )
    assert started.status_code == 302
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = client.get(
        f"/mcp/oauth/{provider_id}/callback",
        params={"state": state, "code": "authorization-code"},
    )
    assert callback.status_code == 200
    assert f"{provider_name} is connected to ArchBro." in callback.text
    assert len(client.get("/mcp/connections", headers=_auth("local-a")).json()) == 1
    assert client.get("/mcp/connections", headers=_auth("local-b")).json() == []


def test_oauth_start_rate_limit_is_scoped_per_principal(dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_SECRET", "slack-secret")
    client = _client(dsn, tmp_path, monkeypatch)

    for _ in range(6):
        started = client.post("/mcp/oauth/slack/start", headers=_auth("local-a"))
        assert started.status_code == 200
        assert started.json()["authorization_url"].startswith("https://slack.com/")

    blocked = client.post("/mcp/oauth/slack/start", headers=_auth("local-a"))
    assert blocked.status_code == 429
    assert "retry shortly" in blocked.json()["detail"]

    other_user = client.post("/mcp/oauth/slack/start", headers=_auth("local-b"))
    assert other_user.status_code == 200


def test_slack_oauth_redirect_uses_the_public_base_only_in_a_local_environment(
    dsn, tmp_path, monkeypatch
):
    """Slack rejects a localhost redirect, so local development borrows a public one."""

    client = _client(dsn, tmp_path, monkeypatch, environment="local")

    status = client.get("/mcp/oauth/slack/status", headers=_auth("local-a"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == (
        "https://archbro-dev.magicdala.com/mcp/oauth/slack/callback"
    )


def test_public_local_environment_keeps_request_host_for_verified_principal(
    dsn, tmp_path, monkeypatch
):
    client = _client(
        dsn,
        tmp_path,
        monkeypatch,
        environment="local",
        base_url="https://archbro-webmcp.magicdala.com",
    )

    status = client.get("/mcp/oauth/slack/status", headers=_auth("prod-user"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == (
        "https://archbro-webmcp.magicdala.com/mcp/oauth/slack/callback"
    )


def test_slack_oauth_redirect_uses_the_request_host_outside_local(
    dsn, tmp_path, monkeypatch
):
    client = _client(
        dsn, tmp_path, monkeypatch, environment="production", base_url="http://testserver"
    )

    status = client.get("/mcp/oauth/slack/status", headers=_auth("prod-user"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == "http://testserver/mcp/oauth/slack/callback"


def test_configured_production_oauth_requires_a_pinned_public_origin(
    dsn, tmp_path, monkeypatch
):
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_SECRET", "slack-secret")
    monkeypatch.delenv("ARCHBRO_OAUTH_REDIRECT_BASE_URL", raising=False)
    monkeypatch.delenv("ARCHBRO_SLACK_OAUTH_REDIRECT_BASE_URL", raising=False)
    client = _client(
        dsn, tmp_path, monkeypatch, environment="production", base_url="http://testserver"
    )

    status = client.get("/mcp/oauth/slack/status", headers=_auth("prod-user"))

    assert status.status_code == 503
    assert "required when provider OAuth is enabled in production" in status.json()["detail"]


def test_configured_production_oauth_uses_the_pinned_public_origin(
    dsn, tmp_path, monkeypatch
):
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_SECRET", "slack-secret")
    monkeypatch.setenv("ARCHBRO_OAUTH_REDIRECT_BASE_URL", "https://archbro-dev.magicdala.com")
    client = _client(
        dsn, tmp_path, monkeypatch, environment="production", base_url="https://untrusted.example"
    )

    status = client.get("/mcp/oauth/slack/status", headers=_auth("prod-user"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == (
        "https://archbro-dev.magicdala.com/mcp/oauth/slack/callback"
    )


def test_production_provider_oauth_rejects_multiple_workers(dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_SECRET", "slack-secret")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    with pytest.raises(RuntimeError, match="process-memory state.*single worker"):
        _client(dsn, tmp_path, monkeypatch, environment="production")
