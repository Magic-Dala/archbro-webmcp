import json

import pytest

from archbro.backend.mcp.provider_gateway import ExternalMcpGateway, McpConnectionConfig


def test_provider_gateway_validates_connections_and_redacts_secrets():
    gateway = ExternalMcpGateway(timeout_seconds=2)
    remote = gateway.add_connection(McpConnectionConfig(
        name="Remote",
        transport="streamable_http",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer secret-token"},
    ))
    local = gateway.add_connection(McpConnectionConfig(
        name="Local",
        transport="streamable_http",
        url="http://127.0.0.1:8082/mcp",
    ))
    stdio = gateway.add_connection(McpConnectionConfig(
        name="stdio",
        transport="stdio",
        command="example-mcp",
        env={"TOKEN": "secret-value"},
    ))

    serialized = json.dumps(gateway.list_connections())
    assert remote["transport"] == "streamable_http"
    assert local["endpoint"] == "http://127.0.0.1:8082/mcp"
    assert stdio["transport"] == "stdio"
    assert "secret-token" not in serialized
    assert "secret-value" not in serialized
    assert "Authorization" not in serialized
    assert "TOKEN" not in serialized

    with pytest.raises(ValueError, match="remote plain HTTP"):
        McpConnectionConfig(name="Unsafe", transport="streamable_http", url="http://example.com/mcp")


def test_github_remote_connections_enforce_official_read_only_header():
    gateway = ExternalMcpGateway(timeout_seconds=2)

    bearer = gateway.add_bearer_connection(
        provider="github",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        access_token="bearer-token",
        auth_type="bearer",
    )
    bearer_headers = gateway._connections[bearer["id"]].config.headers
    assert bearer_headers["Authorization"] == "Bearer bearer-token"
    assert bearer_headers["X-MCP-Readonly"] == "true"

    oauth = gateway.add_oauth_connection(
        provider="github",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        access_token="oauth-token",
        refresh_token="refresh-token",
        expires_in=3600,
        token_url="https://github.com/login/oauth/access_token",
        client_id="client-id",
        client_secret="client-secret",
    )
    oauth_headers = gateway._connections[oauth["id"]].config.headers
    assert oauth_headers["Authorization"] == "Bearer oauth-token"
    assert oauth_headers["X-MCP-Readonly"] == "true"


def test_oauth_prompt_replaces_existing_prompt_and_preserves_query():
    from urllib.parse import parse_qs, urlparse

    github = ExternalMcpGateway._with_oauth_prompt(
        "https://github.com/login/oauth/authorize?client_id=abc&state=xyz&prompt=consent",
        "select_account",
    )
    github_query = parse_qs(urlparse(github).query)
    assert github_query["client_id"] == ["abc"]
    assert github_query["state"] == ["xyz"]
    assert github_query["prompt"] == ["select_account"]

    google = ExternalMcpGateway._with_oauth_prompt(
        "https://accounts.google.com/o/oauth2/auth?client_id=abc&scope=drive",
        "select_account consent",
    )
    google_query = parse_qs(urlparse(google).query)
    assert google_query["prompt"] == ["select_account consent"]


def test_github_oauth_start_forces_account_picker(monkeypatch):
    import archbro.backend.mcp.provider_gateway as provider_gateway

    gateway = ExternalMcpGateway(timeout_seconds=2)
    captured = {}

    def fake_start_persistent_stdio(config):
        captured["config"] = config
        return object()

    monkeypatch.setattr(provider_gateway.shutil, "which", lambda name: "docker" if name == "docker" else None)
    monkeypatch.setattr(gateway, "_start_persistent_stdio", fake_start_persistent_stdio)
    monkeypatch.setattr(gateway, "_state_list_tools", lambda state: [{"name": "get_me"}])
    monkeypatch.setattr(
        gateway,
        "_state_call",
        lambda state, tool_name, arguments: {
            "content": [{
                "type": "text",
                "text": (
                    "Authorize at https://github.com/login/oauth/authorize"
                    "?client_id=abc&state=xyz&redirect_uri=http%3A%2F%2F127.0.0.1%3A8085%2Fcallback"
                ),
            }],
        },
    )

    started = gateway.start_github_oauth_connection()
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(started["authorization_url"]).query)
    assert started["connected"] is False
    assert query["prompt"] == ["select_account"]
    docker_args = captured["config"].args
    readonly_index = docker_args.index("GITHUB_READ_ONLY=1")
    assert docker_args[readonly_index - 1] == "-e"


def test_google_login_url_forces_account_picker_and_consent(monkeypatch):
    from urllib.parse import parse_qs, urlencode, urlparse
    import archbro.backend.mcp.provider_gateway as provider_gateway

    authorization_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode({
        "client_id": "abc",
        "redirect_uri": "http://127.0.0.1:8089/callback",
        "scope": "https://www.googleapis.com/auth/drive",
        "prompt": "consent",
    })

    class FakeProcess:
        pid = 123456
        stdin = None
        stderr = None
        stdout = [authorization_url + "\n"]

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(provider_gateway.shutil, "which", lambda name: "gcloud" if name == "gcloud" else None)
    monkeypatch.setattr(provider_gateway.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(ExternalMcpGateway, "_windows_descendant_process_ids", staticmethod(lambda pid: set()))

    gateway = ExternalMcpGateway(timeout_seconds=2)
    session, result_url = gateway._start_google_gcloud_login()
    try:
        query = parse_qs(urlparse(result_url).query)
        assert query["prompt"] == ["select_account consent"]
        assert query["client_id"] == ["abc"]
    finally:
        session.config_dir.cleanup()


def test_google_drive_scope_check_accepts_readonly_but_not_drive_file(monkeypatch):
    import archbro.backend.mcp.provider_gateway as provider_gateway

    class FakeResponse:
        def __init__(self, scope: str):
            self.scope = scope
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps({"scope": self.scope}).encode("utf-8")

    scopes = iter([
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
    ])
    monkeypatch.setattr(provider_gateway, "urlopen", lambda request, timeout: FakeResponse(next(scopes)))

    assert ExternalMcpGateway._google_token_has_drive_scope("token") is True
    assert ExternalMcpGateway._google_token_has_drive_scope("token") is True
    assert ExternalMcpGateway._google_token_has_drive_scope("token") is False
