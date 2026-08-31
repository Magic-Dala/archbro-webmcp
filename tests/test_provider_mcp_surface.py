from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.mcp.provider_gateway import McpConnectionConfig
from archbro.backend.mcp.provider_oauth import McpOAuthManager
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database

pytestmark = requires_database


def _client(dsn, tmp_path, monkeypatch, *, environment="test") -> TestClient:
    async def principal_provider(token: str) -> TrustedPrincipal:
        if token in {"local-a", "local-b"}:
            return TrustedPrincipal(user_id=token, local_development=True)
        if token == "prod-user":
            return TrustedPrincipal(user_id=token)
        raise ValueError("unknown test token")

    monkeypatch.setenv("ARCHBRO_ENV", environment)
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "local")
    repo = PostgresProjectRepository(dsn)
    return TestClient(build_app(repo, FakeModelProvider(), principal_provider=principal_provider))


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


def test_oauth_callback_state_routes_back_to_the_starting_principal(dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHBRO_SLACK_OAUTH_CLIENT_ID", "slack-client")

    def fake_complete(self, provider_id, *, state, code, redirect_uri):
        connection = self.gateway.add_connection(
            McpConnectionConfig(
                name="Slack",
                transport="streamable_http",
                url="https://mcp.slack.com/mcp",
            )
        )
        return {"provider": provider_id, "connection": connection}

    monkeypatch.setattr(McpOAuthManager, "complete", fake_complete)
    client = _client(dsn, tmp_path, monkeypatch)
    started = client.get(
        "/mcp/oauth/slack/start",
        headers=_auth("local-a"),
        follow_redirects=False,
    )
    assert started.status_code == 302
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = client.get(
        "/mcp/oauth/slack/callback",
        params={"state": state, "code": "authorization-code"},
    )
    assert callback.status_code == 200
    assert "Slack is connected to ArchBro." in callback.text
    assert len(client.get("/mcp/connections", headers=_auth("local-a")).json()) == 1
    assert client.get("/mcp/connections", headers=_auth("local-b")).json() == []


def test_slack_oauth_redirect_uses_the_public_base_only_in_a_local_environment(
    dsn, tmp_path, monkeypatch
):
    """Slack rejects a localhost redirect, so local development borrows a public one.

    The condition has to be the environment itself. It used to be inferred from
    the persistence backend being SQLite, which stopped meaning anything once
    PostgreSQL became the only store -- leaving Slack OAuth quietly broken for
    everyone developing locally.
    """

    client = _client(dsn, tmp_path, monkeypatch, environment="local")

    status = client.get("/mcp/oauth/slack/status", headers=_auth("local-a"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == (
        "https://archbro-dev.magicdala.com/mcp/oauth/slack/callback"
    )


def test_slack_oauth_redirect_uses_the_request_host_outside_local(
    dsn, tmp_path, monkeypatch
):
    client = _client(dsn, tmp_path, monkeypatch, environment="production")

    status = client.get("/mcp/oauth/slack/status", headers=_auth("local-a"))

    assert status.status_code == 200
    assert status.json()["redirect_uri"] == "http://testserver/mcp/oauth/slack/callback"
