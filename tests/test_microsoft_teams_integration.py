from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import archbro.integrations.microsoft_teams as teams_module
from archbro.backend.mcp.provider_oauth import McpOAuthManager
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.repository import ProjectRepository
from archbro.platform.runtime.app import build_app


def make_client() -> TestClient:
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "teams.db"))
    return TestClient(build_app(repo, FakeModelProvider()))


def test_microsoft_teams_status_fails_closed_without_deployment_identity(monkeypatch):
    monkeypatch.delenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_ID", raising=False)
    monkeypatch.delenv("ARCHBRO_MICROSOFT_TEAMS_TENANT_ID", raising=False)
    monkeypatch.delenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_SECRET", raising=False)

    response = make_client().get("/mcp/oauth/microsoft-teams/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["missing_configuration"] == ["client ID", "tenant ID"]
    assert "client_secret" not in response.text


def test_microsoft_teams_pkce_start_uses_tenant_specific_authority(monkeypatch):
    monkeypatch.setenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_ID", "teams-public-client")
    monkeypatch.setenv("ARCHBRO_MICROSOFT_TEAMS_TENANT_ID", "tenant-123")
    monkeypatch.delenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_SECRET", raising=False)

    client = make_client()
    status = client.get("/mcp/oauth/microsoft-teams/status")
    started = client.get("/mcp/oauth/microsoft-teams/start", follow_redirects=False)

    assert status.json()["configured"] is True
    assert started.status_code == 302
    location = started.headers["location"]
    parsed = urlparse(location)
    assert parsed.hostname == "login.microsoftonline.com"
    assert parsed.path == "/tenant-123/oauth2/v2.0/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["teams-public-client"]
    assert query["redirect_uri"] == ["http://testserver/mcp/oauth/microsoft-teams/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query
    assert "Chat.Read" in query["scope"][0]
    assert "ChannelMessage.Send" in query["scope"][0]


def test_microsoft_teams_callback_creates_local_graph_adapter_without_token_leak(monkeypatch):
    monkeypatch.setenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_ID", "teams-public-client")
    monkeypatch.setenv("ARCHBRO_MICROSOFT_TEAMS_TENANT_ID", "tenant-123")
    monkeypatch.delenv("ARCHBRO_MICROSOFT_TEAMS_CLIENT_SECRET", raising=False)

    def fake_exchange(self, provider, payload, **kwargs):
        assert provider.id == "microsoft-teams"
        assert "client_secret" not in payload
        assert kwargs["token_url"].endswith("/tenant-123/oauth2/v2.0/token")
        return {"access_token": "teams-access-secret", "refresh_token": "teams-refresh-secret", "expires_in": 3600}

    monkeypatch.setattr(McpOAuthManager, "_exchange_token", fake_exchange)
    client = make_client()
    started = client.get("/mcp/oauth/microsoft-teams/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = client.get(
        "/mcp/oauth/microsoft-teams/callback",
        params={"state": state, "code": "authorization-code"},
    )

    assert callback.status_code == 200
    assert "Microsoft Teams is connected to ArchBro." in callback.text
    assert "teams-access-secret" not in callback.text
    assert "teams-refresh-secret" not in callback.text
    connection = client.get("/mcp/connections").json()[0]
    assert connection["provider"] == "microsoft-teams"
    assert connection["auth_type"] == "microsoft_teams_oauth"
    assert connection["endpoint"] == "Microsoft Teams · Microsoft Graph v1.0"
    assert "teams-access-secret" not in json.dumps(connection)

    connection_id = connection["id"]
    probe = client.post(f"/mcp/connections/{connection_id}/probe")
    assert probe.status_code == 200
    assert probe.json()["tool_count"] == 7


def test_microsoft_teams_adapter_maps_graph_requests_and_keeps_token_in_headers(monkeypatch):
    calls: list[tuple[str, str, dict[str, str] | None, bytes | None]] = []

    class FakeResponse:
        def __init__(self, body: dict):
            self.body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append((request.method, request.full_url, dict(request.headers), request.data))
        if request.method == "GET":
            return FakeResponse({"value": [{"id": "team-1"}]})
        return FakeResponse({"id": "message-1"})

    monkeypatch.setattr(teams_module, "urlopen", fake_urlopen)
    adapter = teams_module.MicrosoftTeamsGraphAdapter("teams-access-secret")

    listed = adapter.call_tool("teams_list_channels", {"team_id": "team/one", "top": 10})
    sent = adapter.call_tool("teams_send_chat_message", {"chat_id": "chat-1", "content": "hello"})

    assert listed["structuredContent"]["value"][0]["id"] == "team-1"
    assert sent["structuredContent"]["id"] == "message-1"
    assert calls[0][0] == "GET"
    assert "/teams/team%2Fone/channels" in calls[0][1]
    assert "%24top=10" in calls[0][1]
    assert calls[0][2]["Authorization"] == "Bearer teams-access-secret"
    assert calls[1][0] == "POST"
    assert json.loads(calls[1][3].decode("utf-8")) == {"body": {"content": "hello"}}


def test_microsoft_teams_adapter_rejects_unknown_tools_and_unsafe_arguments():
    adapter = teams_module.MicrosoftTeamsGraphAdapter("teams-access-secret")

    try:
        adapter.call_tool("unknown", {})
    except ValueError as exc:
        assert "tool not found" in str(exc)
    else:
        raise AssertionError("unknown Teams tools must fail closed")

    try:
        adapter.call_tool("teams_list_channels", {"team_id": "team-1", "unexpected": "value"})
    except ValueError as exc:
        assert "unsupported Microsoft Teams argument" in str(exc)
    else:
        raise AssertionError("unknown Teams arguments must fail closed")
