from __future__ import annotations

import fnmatch
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class McpGatewayError(RuntimeError):
    """Base error for the generic external MCP gateway."""


class McpGatewayConfigurationError(McpGatewayError):
    pass


class McpServerNotFoundError(McpGatewayError):
    pass


class McpToolNotAllowedError(McpGatewayError):
    pass


@dataclass(frozen=True)
class ConnectedMcpServer:
    id: str
    name: str
    url: str
    description: str = ""
    auth_token_env: str | None = None
    allow_tools: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()

    def is_bound_to(self, project_id: str) -> bool:
        return not self.project_ids or project_id in self.project_ids

    def allows_tool(self, tool_name: str) -> bool:
        return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in self.allow_tools)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "allowed_tool_patterns": list(self.allow_tools),
            "auth_configured": bool(self.auth_token_env and os.getenv(self.auth_token_env)),
        }


SessionFactory = Callable[[ConnectedMcpServer], Any]


def _clean_string(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise McpGatewayConfigurationError(f"{field} must not be empty")
    return text


def _validate_server_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise McpGatewayConfigurationError("MCP server URL must be an absolute http(s) URL")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise McpGatewayConfigurationError("remote MCP server URLs must use https")
    return url


def _parse_server(raw: Any) -> ConnectedMcpServer:
    if not isinstance(raw, dict):
        raise McpGatewayConfigurationError("each MCP server entry must be an object")
    server_id = _clean_string(raw.get("id"), field="server id")
    name = _clean_string(raw.get("name") or server_id, field="server name")
    url = _validate_server_url(_clean_string(raw.get("url"), field="server url"))
    description = str(raw.get("description") or "").strip()
    auth_token_env = str(raw.get("auth_token_env") or "").strip() or None

    allow_raw = raw.get("allow_tools", [])
    if not isinstance(allow_raw, list):
        raise McpGatewayConfigurationError("allow_tools must be a list")
    allow_tools = tuple(str(item).strip() for item in allow_raw if str(item).strip())
    if not allow_tools:
        raise McpGatewayConfigurationError(
            f"MCP server {server_id!r} must explicitly allow at least one tool pattern"
        )

    project_raw = raw.get("project_ids", [])
    if not isinstance(project_raw, list):
        raise McpGatewayConfigurationError("project_ids must be a list")
    project_ids = tuple(str(item).strip() for item in project_raw if str(item).strip())

    return ConnectedMcpServer(
        id=server_id,
        name=name,
        url=url,
        description=description,
        auth_token_env=auth_token_env,
        allow_tools=allow_tools,
        project_ids=project_ids,
    )


def _model_json(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    raise TypeError(f"MCP value is not serializable: {type(value).__name__}")


class ConnectedMcpGateway:
    """Project-scoped gateway for externally configured MCP servers.

    Server URLs and credentials are deployment configuration, never browser input.
    Raw MCP results are returned as evidence; they are not ArchBro canonical state.
    """

    def __init__(
        self,
        servers: list[ConnectedMcpServer] | tuple[ConnectedMcpServer, ...] = (),
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._servers = {server.id: server for server in servers}
        if len(self._servers) != len(servers):
            raise McpGatewayConfigurationError("MCP server ids must be unique")
        self._session_factory = session_factory or self._connect

    @classmethod
    def from_env(cls) -> "ConnectedMcpGateway":
        raw = os.getenv("ARCHBRO_MCP_SERVERS_JSON", "").strip()
        if not raw:
            return cls()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpGatewayConfigurationError("ARCHBRO_MCP_SERVERS_JSON is invalid JSON") from exc
        if not isinstance(payload, list):
            raise McpGatewayConfigurationError("ARCHBRO_MCP_SERVERS_JSON must be a JSON array")
        return cls([_parse_server(item) for item in payload])

    def list_servers(self, project_id: str) -> list[dict[str, Any]]:
        return [
            server.public_dict()
            for server in self._servers.values()
            if server.is_bound_to(project_id)
        ]

    def _server_for(self, project_id: str, server_id: str) -> ConnectedMcpServer:
        server = self._servers.get(server_id)
        if server is None or not server.is_bound_to(project_id):
            raise McpServerNotFoundError(f"MCP server not found for this project: {server_id}")
        return server

    @asynccontextmanager
    async def _connect(self, server: ConnectedMcpServer) -> AsyncIterator[ClientSession]:
        headers: dict[str, str] = {}
        if server.auth_token_env:
            token = os.getenv(server.auth_token_env, "").strip()
            if not token:
                raise McpGatewayConfigurationError(
                    f"credentials are not configured for MCP server {server.id!r}"
                )
            headers["Authorization"] = f"Bearer {token}"

        timeout = httpx.Timeout(30.0, read=300.0)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        ) as http_client:
            async with streamable_http_client(
                server.url,
                http_client=http_client,
            ) as streams:
                read_stream, write_stream, *_ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session

    async def list_tools(self, project_id: str, server_id: str) -> list[dict[str, Any]]:
        server = self._server_for(project_id, server_id)
        tools: list[dict[str, Any]] = []
        async with self._session_factory(server) as session:
            cursor: str | None = None
            while True:
                page = await session.list_tools(cursor) if cursor else await session.list_tools()
                for tool in page.tools:
                    if server.allows_tool(tool.name):
                        tools.append(_model_json(tool))
                cursor = getattr(page, "nextCursor", None)
                if cursor is None:
                    break
        return tools

    async def call_tool(
        self,
        project_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        server = self._server_for(project_id, server_id)
        if not server.allows_tool(tool_name):
            raise McpToolNotAllowedError(
                f"MCP tool {tool_name!r} is not allowed for server {server_id!r}"
            )
        async with self._session_factory(server) as session:
            result = await session.call_tool(tool_name, arguments or {})
        return _model_json(result)
