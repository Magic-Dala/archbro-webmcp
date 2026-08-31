from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from archbro.integrations.google_drive import DRIVE_API_BASE_URL, GoogleDriveApiAdapter
from archbro.integrations.microsoft_teams import MicrosoftTeamsGraphAdapter


MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT_SECONDS = 15.0
GOOGLE_DRIVE_REQUIRED_SCOPE = "https://www.googleapis.com/auth/drive"
GOOGLE_OAUTH_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_AUTH_TIMEOUT_SECONDS = 180.0


class McpConnectionConfig(BaseModel):
    name: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("connection name is required")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> "McpConnectionConfig":
        if self.transport == "streamable_http":
            if not self.url:
                raise ValueError("streamable_http requires url")
            parsed = urlparse(self.url)
            if parsed.username or parsed.password:
                raise ValueError("credentials in MCP URLs are not allowed; use headers")
            host = (parsed.hostname or "").lower()
            is_loopback = host in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme == "https":
                pass
            elif parsed.scheme == "http" and is_loopback:
                pass
            elif parsed.scheme == "http":
                raise ValueError("remote plain HTTP MCP endpoints are rejected by default")
            else:
                raise ValueError("MCP URL must use https, or loopback http for local development")
        else:
            if not (self.command or "").strip():
                raise ValueError("stdio requires command")
            self.command = self.command.strip()
        return self


@dataclass
class _OAuthTokenState:
    provider: str
    token_url: str
    client_id: str
    client_secret: str
    refresh_token: str | None = None
    expires_at: float | None = None


@dataclass
class _PersistentStdioSession:
    process: subprocess.Popen[str]
    output_queue: queue.Queue[str | None]
    next_request_id: int
    lock: threading.Lock


@dataclass
class _ExternalBrowserAuthSession:
    process: subprocess.Popen[str]
    output_queue: queue.Queue[str | None]
    config_dir: Any
    gcloud: str | None = None
    auth_deadline: float | None = None
    process_tree_pids: set[int] = field(default_factory=set)
    cleanup_lock: threading.Lock = field(default_factory=threading.Lock)
    cleaned_up: bool = False


@dataclass
class _ConnectionState:
    id: str
    config: McpConnectionConfig
    tool_count: int | None = None
    last_probe_ok: bool | None = None
    last_error: str | None = None
    oauth: _OAuthTokenState | None = None
    provider: str | None = None
    auth_type: str = "manual"
    stdio_session: _PersistentStdioSession | None = None
    display_endpoint: str | None = None
    authorization_pending: bool = False
    browser_auth_session: _ExternalBrowserAuthSession | None = None
    google_access_token: str | None = None
    drive_adapter: GoogleDriveApiAdapter | None = None
    graph_adapter: MicrosoftTeamsGraphAdapter | None = None


class ExternalMcpGateway:
    """Human-configured, memory-only MCP connection gateway."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        google_auth_timeout_seconds: float = GOOGLE_AUTH_TIMEOUT_SECONDS,
        reuse_existing_google_credential: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if google_auth_timeout_seconds <= 0:
            raise ValueError("google_auth_timeout_seconds must be greater than zero")
        self.timeout_seconds = timeout_seconds
        self.google_auth_timeout_seconds = google_auth_timeout_seconds
        self.reuse_existing_google_credential = reuse_existing_google_credential
        self._connections: dict[str, _ConnectionState] = {}

    def add_connection(self, config: McpConnectionConfig) -> dict[str, Any]:
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(id=connection_id, config=config)
        self._connections[connection_id] = state
        return self._public(state)

    def add_bearer_connection(
        self,
        *,
        provider: str,
        name: str,
        url: str,
        access_token: str,
        auth_type: str,
    ) -> dict[str, Any]:
        provider = provider.strip()
        token = access_token.strip()
        if not provider:
            raise ValueError("provider is required")
        if not token:
            raise ValueError("access token is required")
        self._remove_provider_connections(provider, endpoint=url, name=name)
        config = McpConnectionConfig(
            name=name,
            transport="streamable_http",
            url=url,
            headers={"Authorization": f"Bearer {token}"},
        )
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type=auth_type.strip() or "bearer",
        )
        self._connections[connection_id] = state
        return self._public(state)

    def start_github_oauth_connection(self) -> dict[str, Any]:
        if shutil.which("docker") is None:
            raise RuntimeError("Docker is required for the official GitHub MCP OAuth runtime")
        provider = "github"
        name = "GitHub"
        remote_endpoint = "https://api.githubcopilot.com/mcp/"
        self._remove_provider_connections(provider, endpoint=remote_endpoint, name=name)
        config = McpConnectionConfig(
            name=name,
            transport="stdio",
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "-p",
                "127.0.0.1:8085:8085",
                "-e",
                "GITHUB_OAUTH_CALLBACK_PORT=8085",
                "ghcr.io/github/github-mcp-server",
            ],
            env={},
        )
        session = self._start_persistent_stdio(config)
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type="github_oauth",
            stdio_session=session,
            display_endpoint="GitHub official MCP · OAuth",
            authorization_pending=True,
        )
        self._connections[connection_id] = state
        try:
            tools = self._state_list_tools(state)
            state.tool_count = len(tools)
            result = self._state_call(state, "get_me", {})
            authorization_url = self._extract_github_authorization_url(result)
            if authorization_url:
                authorization_url = self._with_oauth_prompt(authorization_url, "select_account")
            if authorization_url:
                return {
                    "connected": False,
                    "authorization_url": authorization_url,
                    "connection": self._public(state),
                }
            state.authorization_pending = False
            state.last_probe_ok = True
            return {
                "connected": True,
                "authorization_url": None,
                "tool_count": state.tool_count,
                "connection": self._public(state),
            }
        except Exception:
            self.remove_connection(connection_id)
            raise

    def poll_github_oauth(self, connection_id: str) -> dict[str, Any]:
        state = self._get(connection_id)
        if state.provider != "github" or state.auth_type != "github_oauth" or state.stdio_session is None:
            raise ValueError("connection is not a GitHub OAuth runtime")
        result = self._state_call(state, "get_me", {})
        authorization_url = self._extract_github_authorization_url(result)
        if authorization_url:
            state.authorization_pending = True
            return {"connected": False, "connection": self._public(state)}
        tools = self._state_list_tools(state)
        state.tool_count = len(tools)
        state.last_probe_ok = True
        state.last_error = None
        state.authorization_pending = False
        return {
            "connected": True,
            "tool_count": len(tools),
            "connection": self._public(state),
        }

    def start_google_drive_oauth_connection(self) -> dict[str, Any]:
        gcloud = shutil.which("gcloud")
        if gcloud is None:
            raise RuntimeError("Google Drive sign-in helper is unavailable; install the Google Cloud CLI")
        provider = "google-drive"
        name = "Google Drive"
        endpoint = DRIVE_API_BASE_URL
        self._remove_provider_connections(provider, endpoint=endpoint, name=name)
        if self.reuse_existing_google_credential:
            access_token = self._existing_gcloud_access_token(gcloud)
            if access_token and self._google_token_has_drive_scope(access_token):
                try:
                    return self._connected_google_drive_result(
                        access_token=access_token,
                        provider=provider,
                        name=name,
                        endpoint=endpoint,
                    )
                except (RuntimeError, ValueError):
                    pass
        session, authorization_url = self._start_google_gcloud_login()
        config = McpConnectionConfig(
            name=name,
            transport="streamable_http",
            url=endpoint,
            headers={},
        )
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type="google_drive_oauth",
            browser_auth_session=session,
            display_endpoint="Google Drive API · OAuth",
            authorization_pending=True,
        )
        self._connections[connection_id] = state
        return {
            "connected": False,
            "authorization_url": authorization_url,
            "connection": self._public(state),
        }

    def _connected_google_drive_result(
        self,
        *,
        access_token: str,
        provider: str,
        name: str,
        endpoint: str,
    ) -> dict[str, Any]:
        config = McpConnectionConfig(
            name=name,
            transport="streamable_http",
            url=endpoint,
            headers={},
        )
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type="google_drive_oauth",
            display_endpoint="Google Drive API · OAuth",
            google_access_token=access_token,
            drive_adapter=GoogleDriveApiAdapter(access_token, timeout_seconds=self.timeout_seconds),
        )
        self._connections[connection_id] = state
        try:
            tools = self._state_list_tools(state)
            self._probe_google_drive(state)
        except Exception:
            self.remove_connection(connection_id)
            raise
        state.tool_count = len(tools)
        state.last_probe_ok = True
        return {
            "connected": True,
            "authorization_url": None,
            "tool_count": len(tools),
            "connection": self._public(state),
        }

    def poll_google_drive_oauth(self, connection_id: str) -> dict[str, Any]:
        state = self._get(connection_id)
        if state.provider != "google-drive" or state.auth_type not in {"google_gcloud", "google_drive_oauth"}:
            raise ValueError("connection is not a Google Drive OAuth runtime")

        if not state.authorization_pending:
            if state.last_probe_ok is True and state.google_access_token:
                return {
                    "connected": True,
                    "tool_count": state.tool_count,
                    "connection": self._public(state),
                }
            if state.last_error:
                raise RuntimeError(state.last_error)
            raise RuntimeError("Google Drive authorization is unavailable; reconnect the provider")

        session = state.browser_auth_session
        if session is None:
            raise RuntimeError("Google Drive authorization is unavailable; reconnect the provider")
        if session.auth_deadline is not None and time.monotonic() >= session.auth_deadline:
            message = "Google authorization timed out; retry Google Drive sign-in"
            self._fail_google_drive_auth(state, message)
            raise RuntimeError(message)

        return_code = session.process.poll()
        if return_code is None:
            return {"connected": False, "connection": self._public(state)}
        if return_code != 0:
            message = "Google authorization was not completed"
            self._fail_google_drive_auth(state, message)
            raise RuntimeError(message)

        try:
            token = self._gcloud_access_token(session)
            state.google_access_token = token
            state.drive_adapter = GoogleDriveApiAdapter(token, timeout_seconds=self.timeout_seconds)
            self._cleanup_google_auth_session(state)
            state.authorization_pending = False
            tools = self._state_list_tools(state)
            self._probe_google_drive(state)
        except Exception as exc:
            state.last_probe_ok = False
            state.last_error = self._google_drive_error(exc)
            state.authorization_pending = False
            self._cleanup_google_auth_session(state)
            state.google_access_token = None
            state.drive_adapter = None
            state.config.headers.pop("Authorization", None)
            raise RuntimeError(state.last_error) from None
        state.tool_count = len(tools)
        state.last_probe_ok = True
        state.last_error = None
        return {
            "connected": True,
            "tool_count": len(tools),
            "connection": self._public(state),
        }

    def add_oauth_connection(
        self,
        *,
        provider: str,
        name: str,
        url: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
        token_url: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        provider = provider.strip()
        if not provider:
            raise ValueError("OAuth provider is required")
        token = access_token.strip()
        if not token:
            raise ValueError("OAuth access token is required")
        self._remove_provider_connections(provider, endpoint=url, name=name)
        config = McpConnectionConfig(
            name=name,
            transport="streamable_http",
            url=url,
            headers={"Authorization": f"Bearer {token}"},
        )
        expires_at = time.time() + expires_in if expires_in else None
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type="oauth",
            oauth=_OAuthTokenState(
                provider=provider,
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                expires_at=expires_at,
            ),
        )
        self._connections[connection_id] = state
        return self._public(state)

    def add_microsoft_teams_oauth_connection(
        self,
        *,
        provider: str,
        name: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
        token_url: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        """Add the local MCP-shaped Teams adapter after Microsoft OAuth."""
        provider = provider.strip()
        token = access_token.strip()
        if provider != "microsoft-teams":
            raise ValueError("Microsoft Teams OAuth provider is required")
        if not token:
            raise ValueError("Microsoft Teams access token is required")
        endpoint = "https://graph.microsoft.com/v1.0"
        self._remove_provider_connections(provider, endpoint=endpoint, name=name)
        config = McpConnectionConfig(
            name=name,
            transport="streamable_http",
            url=endpoint,
            headers={"Authorization": f"Bearer {token}"},
        )
        expires_at = time.time() + expires_in if expires_in else None
        connection_id = f"mcp_{uuid4().hex}"
        state = _ConnectionState(
            id=connection_id,
            config=config,
            provider=provider,
            auth_type="microsoft_teams_oauth",
            oauth=_OAuthTokenState(
                provider=provider,
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                expires_at=expires_at,
            ),
            display_endpoint="Microsoft Teams · Microsoft Graph v1.0",
            graph_adapter=MicrosoftTeamsGraphAdapter(token, timeout_seconds=self.timeout_seconds),
        )
        self._connections[connection_id] = state
        return self._public(state)

    def _remove_provider_connections(self, provider: str, *, endpoint: str, name: str) -> None:
        for connection_id, existing in list(self._connections.items()):
            same_provider = existing.provider == provider or (existing.oauth and existing.oauth.provider == provider)
            same_legacy_connection = (
                existing.config.transport == "streamable_http"
                and existing.config.url == endpoint
                and existing.config.name == name
            )
            if same_provider or same_legacy_connection:
                self.remove_connection(connection_id)

    def remove_connection(self, connection_id: str) -> bool:
        state = self._connections.pop(connection_id, None)
        if state is None:
            return False
        if state.stdio_session is not None:
            self._stop_persistent_stdio(state.stdio_session)
        if state.browser_auth_session is not None:
            self._stop_browser_auth_session(state.browser_auth_session)
        return True

    def list_connections(self) -> list[dict[str, Any]]:
        return [self._public(state) for state in self._connections.values()]

    def probe(self, connection_id: str) -> dict[str, Any]:
        state = self._get(connection_id)
        try:
            self._ensure_fresh_oauth(state)
            tools = self._state_list_tools(state)
        except Exception as exc:
            state.last_probe_ok = False
            state.last_error = self._safe_error(exc)
            raise RuntimeError(state.last_error) from None
        state.tool_count = len(tools)
        state.last_probe_ok = True
        state.last_error = None
        return {"ok": True, "tool_count": len(tools), "connection": self._public(state)}

    def list_tools(self, connection_id: str) -> dict[str, Any]:
        state = self._get(connection_id)
        self._ensure_fresh_oauth(state)
        tools = self._state_list_tools(state)
        state.tool_count = len(tools)
        state.last_probe_ok = True
        state.last_error = None
        return {"connection_id": connection_id, "tools": tools, "tool_count": len(tools)}

    def call_tool(self, connection_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self._get(connection_id)
        self._ensure_fresh_oauth(state)
        name = (tool_name or "").strip()
        if not name:
            raise ValueError("tool_name is required")
        result = self._state_call(state, name, arguments or {})
        return {
            "connection_id": connection_id,
            "tool_name": name,
            "external_evidence": result,
            "canonical_state_mutated": False,
        }

    def _get(self, connection_id: str) -> _ConnectionState:
        try:
            return self._connections[connection_id]
        except KeyError:
            raise KeyError(f"MCP connection not found: {connection_id}") from None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip() or type(exc).__name__
        return message[:500]

    @staticmethod
    def _public(state: _ConnectionState) -> dict[str, Any]:
        config = state.config
        endpoint = state.display_endpoint or (config.url if config.transport == "streamable_http" else (config.command or ""))
        return {
            "id": state.id,
            "name": config.name,
            "transport": config.transport,
            "endpoint": endpoint,
            "tool_count": state.tool_count,
            "last_probe_ok": state.last_probe_ok,
            "last_error": state.last_error,
            "has_credentials": bool(config.headers) or bool(state.google_access_token) or any(
                marker in key.upper() for key in config.env for marker in ("TOKEN", "SECRET", "KEY")
            ),
            "auth_type": state.auth_type,
            "provider": state.provider,
            "authorization_pending": state.authorization_pending,
        }

    def _ensure_fresh_oauth(self, state: _ConnectionState) -> None:
        if state.auth_type in {"google_gcloud", "google_drive_oauth"}:
            if state.authorization_pending:
                raise RuntimeError("Google Drive authorization is still pending")
            if not state.google_access_token:
                raise RuntimeError("Google Drive authorization is unavailable; reconnect the provider")
            if state.drive_adapter is not None:
                state.drive_adapter.update_access_token(state.google_access_token)
            else:
                # Keep legacy in-memory connections callable during a rolling
                # restart. New Google Drive connections always use the direct
                # API adapter above and do not send an MCP project header.
                state.config.headers["Authorization"] = f"Bearer {state.google_access_token}"
            return
        oauth = state.oauth
        if oauth is None or oauth.expires_at is None or oauth.expires_at > time.time() + 60:
            return
        if not oauth.refresh_token:
            raise RuntimeError(f"{oauth.provider} OAuth session expired; reconnect the provider")
        payload = {
            "client_id": oauth.client_id,
            "refresh_token": oauth.refresh_token,
            "grant_type": "refresh_token",
        }
        if oauth.client_secret:
            payload["client_secret"] = oauth.client_secret
        request = Request(
            oauth.token_url,
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
            raise RuntimeError(f"{oauth.provider} OAuth refresh failed with HTTP {exc.code}") from None
        except URLError as exc:
            raise RuntimeError(f"{oauth.provider} OAuth refresh failed: {exc.reason}") from None
        try:
            token = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"{oauth.provider} OAuth refresh returned invalid JSON") from None
        if not isinstance(token, dict):
            raise RuntimeError(f"{oauth.provider} OAuth refresh returned an invalid response")
        if token.get("error") or token.get("ok") is False:
            error = str(token.get("error_description") or token.get("error") or "refresh failed")[:200]
            raise RuntimeError(f"{oauth.provider} OAuth refresh failed: {error}")
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"{oauth.provider} OAuth refresh returned no access token")
        state.config.headers["Authorization"] = f"Bearer {access_token}"
        if state.graph_adapter is not None:
            state.graph_adapter.update_access_token(access_token)
        refreshed = str(token.get("refresh_token") or "").strip()
        if refreshed:
            oauth.refresh_token = refreshed
        try:
            expires_in = int(token.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        oauth.expires_at = time.time() + max(60, expires_in)

    def _list_tools(self, config: McpConnectionConfig) -> list[dict[str, Any]]:
        result = self._request(config, "tools/list", {})
        return self._tools_from_result(result)

    @staticmethod
    def _probe_google_drive(state: _ConnectionState) -> None:
        adapter = state.drive_adapter
        if adapter is None:
            return
        result = adapter.call_tool("list_recent_files", {"pageSize": 1})
        if not isinstance(result, dict) or result.get("isError") is not True:
            return
        detail = ""
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                detail = str(item.get("text") or "").strip()
                if detail:
                    break
        raise RuntimeError(detail[:500] or "Google Drive authorization was denied")

    def _state_list_tools(self, state: _ConnectionState) -> list[dict[str, Any]]:
        if state.drive_adapter is not None:
            return state.drive_adapter.list_tools()
        if state.graph_adapter is not None:
            return state.graph_adapter.list_tools()
        if state.stdio_session is not None:
            result = self._persistent_stdio_request(state.stdio_session, "tools/list", {})
            return self._tools_from_result(result)
        return self._list_tools(state.config)

    @staticmethod
    def _tools_from_result(result: Any) -> list[dict[str, Any]]:
        tools = result.get("tools", []) if isinstance(result, dict) else []
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list returned an invalid tools payload")
        return tools

    def _call(self, config: McpConnectionConfig, tool_name: str, arguments: dict[str, Any]) -> Any:
        return self._request(config, "tools/call", {"name": tool_name, "arguments": arguments})

    def _state_call(self, state: _ConnectionState, tool_name: str, arguments: dict[str, Any]) -> Any:
        if state.drive_adapter is not None:
            return state.drive_adapter.call_tool(tool_name, arguments)
        if state.graph_adapter is not None:
            return state.graph_adapter.call_tool(tool_name, arguments)
        if state.stdio_session is not None:
            return self._persistent_stdio_request(
                state.stdio_session,
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        return self._call(state.config, tool_name, arguments)

    @staticmethod
    def _with_oauth_prompt(url: str, prompt: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["prompt"] = [prompt]
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()

    @staticmethod
    def _extract_github_authorization_url(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        for item in result.get("content", []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            for match in re.finditer(r"https://github\.com/[^\s<>\"']+", text):
                candidate = match.group(0).rstrip(".,);]")
                parsed = urlparse(candidate)
                if parsed.path != "/login/oauth/authorize" or not parsed.query:
                    continue
                query = parse_qs(parsed.query)
                if "client_id" in query and ("state" in query or "redirect_uri" in query):
                    return candidate
        return None

    def _request(self, config: McpConnectionConfig, method: str, params: dict[str, Any]) -> Any:
        if config.transport == "streamable_http":
            return self._http_request(config, method, params)
        return self._stdio_request(config, method, params)

    def _http_request(self, config: McpConnectionConfig, method: str, params: dict[str, Any]) -> Any:
        assert config.url is not None
        session_id: str | None = None
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "archbro", "version": "0.1.0"},
            },
        }
        initialized_result, session_id = self._http_post(config, initialize, session_id=session_id)
        if not isinstance(initialized_result, dict):
            raise RuntimeError("MCP initialize returned an invalid response")
        self._http_post(
            config,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=session_id,
            expect_response=False,
        )
        response, _ = self._http_post(
            config,
            {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            session_id=session_id,
        )
        return response

    def _http_post(
        self,
        config: McpConnectionConfig,
        payload: dict[str, Any],
        *,
        session_id: str | None,
        expect_response: bool = True,
    ) -> tuple[Any, str | None]:
        assert config.url is not None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "User-Agent": "ArchBro/0.1",
            **config.headers,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = Request(
            config.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                next_session_id = response.headers.get("Mcp-Session-Id") or session_id
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            detail = self._safe_http_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            if exc.code in {401, 403}:
                raise RuntimeError(f"MCP HTTP {exc.code}{suffix}: authentication required or denied") from None
            raise RuntimeError(f"MCP HTTP {exc.code}{suffix}") from None
        except URLError as exc:
            raise RuntimeError(f"MCP connection failed: {exc.reason}") from None

        if not expect_response or status == 202 or not body.strip():
            return None, next_session_id
        message = self._decode_http_message(body, content_type)
        if "error" in message:
            error = message["error"]
            raise RuntimeError(f"MCP error {error.get('code', '')}: {error.get('message', 'request failed')}")
        if "result" not in message:
            raise RuntimeError("MCP response did not contain result")
        return message["result"], next_session_id

    def google_drive_readiness(self, *, gcloud: str | None = None) -> dict[str, Any]:
        """Return local prerequisites for the Drive-only OAuth adapter.

        The Google Cloud CLI is used only as a local, browser-based OAuth
        helper. ArchBro does not call the hosted Drive MCP service, inspect a
        Cloud project, enable APIs, or require the MCP Tool User IAM role.
        """

        gcloud = gcloud or shutil.which("gcloud")
        base: dict[str, Any] = {
            "gcloud_available": bool(gcloud),
            "project_configured": False,
            "apis_checked": False,
            "missing_apis": [],
            "ready": bool(gcloud),
        }
        if not gcloud:
            base["message"] = "Google Drive sign-in helper is unavailable; install the Google Cloud CLI."
            return base
        base["message"] = (
            "Google Drive OAuth is ready. ArchBro uses the Drive API directly; "
            "no Google Cloud project, Drive MCP service, or IAM role is required."
        )
        return base

    @staticmethod
    def _google_drive_error(exc: Exception) -> str:
        if isinstance(exc, RuntimeError) and "insufficient_scope" in str(exc).lower():
            return (
                "Google login succeeded, but this OAuth token is missing Drive access. "
                "Reconnect Google Drive and approve Drive access; if it still fails, add the Drive scopes "
                "to the Google OAuth consent screen first."
            )
        if isinstance(exc, RuntimeError) and "MCP HTTP 403" in str(exc):
            return (
                "Google authorization succeeded, but Google Drive denied this account (HTTP 403). "
                "Check that the signed-in account can access Google Drive and reconnect."
            )
        if isinstance(exc, RuntimeError) and "MCP HTTP 401" in str(exc):
            return "Google authorization succeeded, but Google Drive rejected the access token (HTTP 401)."
        if isinstance(exc, RuntimeError) and "Google Drive request failed with HTTP 403" in str(exc):
            return (
                "Google authorization succeeded, but Google Drive denied this account (HTTP 403). "
                "Check that the signed-in account can access Google Drive and reconnect."
            )
        if isinstance(exc, RuntimeError) and "Google Drive request failed with HTTP 401" in str(exc):
            return "Google authorization succeeded, but Google Drive rejected the access token (HTTP 401)."
        if isinstance(exc, RuntimeError) and "Google Drive request failed" in str(exc):
            return "Google authorization succeeded, but the Google Drive API request failed. Reconnect and retry."
        if isinstance(exc, RuntimeError) and "MCP" in str(exc):
            return (
                "Google authorization succeeded, but the Drive API request failed. "
                "Reconnect Google Drive and retry."
            )
        return "Google authorization could not be completed; retry Google Drive sign-in."

    def _fail_google_drive_auth(self, state: _ConnectionState, message: str) -> None:
        state.last_probe_ok = False
        state.last_error = message[:500]
        state.authorization_pending = False
        state.google_access_token = None
        state.drive_adapter = None
        state.config.headers.pop("Authorization", None)
        self._cleanup_google_auth_session(state)

    def _cleanup_google_auth_session(self, state: _ConnectionState) -> None:
        session = state.browser_auth_session
        if session is None:
            return
        self._stop_browser_auth_session(session)
        state.browser_auth_session = None

    @staticmethod
    def _decode_http_message(body: str, content_type: str) -> dict[str, Any]:
        if "text/event-stream" in content_type or body.lstrip().startswith(("event:", "data:")):
            for line in body.splitlines():
                if line.startswith("data:"):
                    candidate = line[5:].strip()
                    if candidate:
                        value = json.loads(candidate)
                        if isinstance(value, dict) and ("result" in value or "error" in value):
                            return value
            raise RuntimeError("MCP SSE response contained no JSON-RPC result")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise RuntimeError("MCP HTTP response was not a JSON object")
        return value

    @staticmethod
    def _safe_http_error_detail(exc: HTTPError) -> str:
        headers = getattr(exc, "headers", None)
        challenge = str(headers.get("WWW-Authenticate") or "") if headers is not None else ""
        if re.search(r'(?i)error\s*=\s*"insufficient_scope"', challenge):
            required_scope = re.search(r'(?i)scope\s*=\s*"([^"]+)"', challenge)
            detail = "insufficient_scope"
            if required_scope:
                detail += f" (required scope: {required_scope.group(1)})"
            return detail[:200]
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except (OSError, UnicodeError):
            return ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            detail = re.sub(r"\s+", " ", body).strip()
            if not detail or detail.startswith("<"):
                return ""
            detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [redacted]", detail)
            return detail[:200]
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return ""
        status = str(error.get("status") or "").strip()
        message = str(error.get("message") or "").strip()
        detail = ": ".join(part for part in (status, message) if part)
        detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [redacted]", detail)
        detail = re.sub(
            r"(?i)(access[_-]?token|refresh[_-]?token|authorization|code)[=:][^\s,;]+",
            r"\1=[redacted]",
            detail,
        )
        return detail[:200]

    def _start_google_gcloud_login(self) -> tuple[_ExternalBrowserAuthSession, str]:
        gcloud = shutil.which("gcloud")
        if not gcloud:
            raise RuntimeError("Google Drive sign-in helper is unavailable; install the Google Cloud CLI")

        config_dir = tempfile.TemporaryDirectory(prefix="archbro-google-oauth-")
        env = os.environ.copy()
        env["CLOUDSDK_CONFIG"] = config_dir.name
        env["BROWSER"] = "echo"
        try:
            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "env": env,
                "bufsize": 1,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(
                [gcloud, "auth", "login", "--enable-gdrive-access", "--force", "--brief"],
                **popen_kwargs,
            )
        except OSError as exc:
            config_dir.cleanup()
            raise RuntimeError(f"failed to start Google authorization runtime: {exc}") from None

        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        threading.Thread(target=read_output, daemon=True).start()
        session = _ExternalBrowserAuthSession(
            process=process,
            output_queue=output_queue,
            config_dir=config_dir,
            gcloud=gcloud,
        )
        deadline = time.monotonic() + min(12.0, max(5.0, self.timeout_seconds))
        authorization_url: str | None = None
        try:
            while time.monotonic() < deadline:
                timeout = max(0.1, min(1.0, deadline - time.monotonic()))
                try:
                    line = output_queue.get(timeout=timeout)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                match = re.search(r"https://accounts\.google\.com/[^\s<>\"']+", line)
                if match:
                    authorization_url = match.group(0).rstrip(".,);]")
                    break
            if not authorization_url:
                raise RuntimeError("Google authorization runtime did not return a sign-in URL")
            parsed = urlparse(authorization_url)
            if parsed.scheme != "https" or parsed.hostname != "accounts.google.com":
                raise RuntimeError("Google authorization runtime returned an unexpected sign-in host")
            query = parse_qs(parsed.query)
            redirect = (query.get("redirect_uri") or [""])[0]
            redirect_parsed = urlparse(redirect)
            if redirect_parsed.scheme != "http" or redirect_parsed.hostname not in {"localhost", "127.0.0.1"}:
                raise RuntimeError("Google authorization runtime returned a non-loopback callback")
            scopes = " ".join(query.get("scope") or [])
            if "https://www.googleapis.com/auth/drive" not in scopes:
                raise RuntimeError("Google authorization runtime did not request Drive access")
            authorization_url = self._with_oauth_prompt(authorization_url, "select_account consent")
            session.process_tree_pids.update(self._windows_descendant_process_ids(process.pid))
            session.auth_deadline = time.monotonic() + self.google_auth_timeout_seconds
            return session, authorization_url
        except Exception:
            self._stop_browser_auth_session(session)
            raise

    def _gcloud_access_token(self, session: _ExternalBrowserAuthSession) -> str:
        gcloud = session.gcloud or shutil.which("gcloud")
        if not gcloud:
            raise RuntimeError("Google Cloud CLI is unavailable")
        token = self._existing_gcloud_access_token(gcloud, config_dir=session.config_dir.name)
        if not token:
            raise RuntimeError("Google authorization token is unavailable")
        if not self._google_token_has_drive_scope(token):
            raise RuntimeError(
                "Google authorization completed, but the access token has no Drive OAuth scope. "
                "Reconnect Google Drive and approve Drive access."
            )
        return token

    @staticmethod
    def _existing_gcloud_access_token(gcloud: str, *, config_dir: str | None = None) -> str | None:
        env = os.environ.copy()
        if config_dir:
            env["CLOUDSDK_CONFIG"] = config_dir
        try:
            result = subprocess.run(
                [gcloud, "auth", "print-access-token"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        token = result.stdout.strip() if result.returncode == 0 else ""
        return token or None

    @staticmethod
    def _google_token_has_drive_scope(access_token: str) -> bool:
        """Check the Google OAuth token's granted scopes without exposing it."""

        token = access_token.strip()
        if not token:
            return False
        request = Request(
            f"{GOOGLE_OAUTH_TOKENINFO_URL}?{urlencode({'access_token': token})}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (HTTPError, OSError, URLError, TimeoutError):
            return False
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        scopes = payload.get("scope")
        if not isinstance(scopes, str):
            return False
        return GOOGLE_DRIVE_REQUIRED_SCOPE in set(scopes.split())

    @staticmethod
    def _stop_browser_auth_session(session: _ExternalBrowserAuthSession) -> None:
        """Stop only the process tree spawned for this Google auth session."""

        with session.cleanup_lock:
            if session.cleaned_up:
                return
            process = session.process
            process_id = getattr(process, "pid", None)

            if os.name == "nt" and process_id:
                session.process_tree_pids.update(ExternalMcpGateway._windows_descendant_process_ids(process_id))
                ExternalMcpGateway._windows_taskkill(process_id, tree=True)
                for child_id in sorted(session.process_tree_pids, reverse=True):
                    ExternalMcpGateway._windows_taskkill(child_id, tree=False)
            elif os.name != "nt" and process_id:
                try:
                    os.killpg(os.getpgid(process_id), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass

            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "nt" and process_id:
                    ExternalMcpGateway._windows_taskkill(process_id, tree=True)
                    for child_id in sorted(session.process_tree_pids, reverse=True):
                        ExternalMcpGateway._windows_taskkill(child_id, tree=False)
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except (AttributeError, OSError):
                        pass
            try:
                session.config_dir.cleanup()
            except Exception:
                pass
            session.cleaned_up = process.poll() is not None

    @staticmethod
    def _windows_taskkill(process_id: int, *, tree: bool) -> None:
        taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
        if not taskkill:
            candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
            if os.path.exists(candidate):
                taskkill = candidate
        if not taskkill:
            return
        command = [taskkill, "/PID", str(process_id)]
        if tree:
            command.append("/T")
        command.append("/F")
        try:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _windows_descendant_process_ids(process_id: int) -> set[int]:
        if os.name != "nt" or not process_id:
            return set()
        try:
            from ctypes import wintypes

            class ProcessEntry32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            invalid_handle = ctypes.c_void_p(-1).value
            snapshot_value = getattr(snapshot, "value", snapshot)
            if snapshot_value in {None, invalid_handle}:
                return set()
            try:
                entry = ProcessEntry32W()
                entry.dwSize = ctypes.sizeof(ProcessEntry32W)
                processes: dict[int, int] = {}
                if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    while True:
                        processes[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                        if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                            break
                descendants: set[int] = set()
                pending = [process_id]
                while pending:
                    parent_id = pending.pop()
                    for child_id, candidate_parent_id in processes.items():
                        if candidate_parent_id == parent_id and child_id not in descendants:
                            descendants.add(child_id)
                            pending.append(child_id)
                return descendants
            finally:
                kernel32.CloseHandle(snapshot)
        except (AttributeError, OSError, TypeError, ValueError):
            return set()

    def _start_persistent_stdio(self, config: McpConnectionConfig) -> _PersistentStdioSession:
        assert config.command is not None
        env = os.environ.copy()
        env.update(config.env)
        try:
            process = subprocess.Popen(
                [config.command, *config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to start MCP stdio process: {exc}") from None
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        session = _PersistentStdioSession(
            process=process,
            output_queue=output_queue,
            next_request_id=2,
            lock=threading.Lock(),
        )
        try:
            self._stdio_send(process, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ArchBro", "version": "0.1"},
                },
            })
            self._stdio_wait(output_queue, 1)
            self._stdio_send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            return session
        except Exception:
            self._stop_persistent_stdio(session)
            raise

    def _persistent_stdio_request(
        self,
        session: _PersistentStdioSession,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        with session.lock:
            if session.process.poll() is not None:
                raise RuntimeError("MCP stdio process is not running")
            request_id = session.next_request_id
            session.next_request_id += 1
            self._stdio_send(
                session.process,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            )
            return self._stdio_wait(session.output_queue, request_id)

    @staticmethod
    def _stop_persistent_stdio(session: _PersistentStdioSession) -> None:
        process = session.process
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    def _stdio_request(self, config: McpConnectionConfig, method: str, params: dict[str, Any]) -> Any:
        assert config.command is not None
        env = os.environ.copy()
        env.update(config.env)
        process = subprocess.Popen(
            [config.command, *config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        try:
            self._stdio_send(process, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "archbro", "version": "0.1.0"},
                },
            })
            self._stdio_wait(output_queue, 1)
            self._stdio_send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._stdio_send(process, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
            return self._stdio_wait(output_queue, 2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

    @staticmethod
    def _stdio_send(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP stdio stdin is unavailable")
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _stdio_wait(self, output_queue: queue.Queue[str | None], request_id: int) -> Any:
        while True:
            try:
                line = output_queue.get(timeout=self.timeout_seconds)
            except queue.Empty:
                raise RuntimeError("MCP stdio response timed out") from None
            if line is None:
                raise RuntimeError("MCP stdio process exited before responding")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise RuntimeError(f"MCP error {error.get('code', '')}: {error.get('message', 'request failed')}")
            return message.get("result")
