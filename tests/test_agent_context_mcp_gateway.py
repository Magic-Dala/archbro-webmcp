from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.mcp.gateway import (
    ConnectedMcpGateway,
    ConnectedMcpServer,
    McpToolNotAllowedError,
)
from archbro.platform.persistence.repository import ProjectRepository
from archbro.platform.runtime.app import build_app


def make_client() -> TestClient:
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "agent-context.db"))
    return TestClient(build_app(repo, FakeModelProvider()))


def create_project_with_architecture(client: TestClient) -> str:
    project = client.post(
        "/projects",
        json={
            "name": "Context Demo",
            "goal": "Build a React UI with a FastAPI backend and managed persistence.",
            "description": "",
        },
    )
    assert project.status_code == 200
    project_id = project.json()["id"]
    bootstrap = client.post(
        f"/projects/{project_id}/interactive-initial-architecture",
        json={
            "architecture": {
                "version": 1,
                "summary": "React calls FastAPI; backend owns privileged operations.",
                "components": [
                    {
                        "id": "web",
                        "name": "React Web",
                        "type": "frontend",
                        "responsibility": "Human workspace",
                    },
                    {
                        "id": "api",
                        "name": "FastAPI",
                        "type": "backend",
                        "responsibility": "Product API",
                    },
                ],
                "relationships": [],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [
                {"title": "Build API boundary", "related_component": "api"},
                {"title": "Build web workspace", "related_component": "web"},
            ],
            "reasoning": "Host-generated architecture for context testing.",
        },
    )
    assert bootstrap.status_code == 200
    return project_id


def test_agent_context_is_compact_projection_and_lists_bound_sources(monkeypatch):
    monkeypatch.setenv(
        "ARCHBRO_MCP_SERVERS_JSON",
        json.dumps(
            [
                {
                    "id": "github",
                    "name": "GitHub",
                    "url": "https://example.com/mcp",
                    "description": "Repository evidence",
                    "allow_tools": ["search_*", "get_*"],
                }
            ]
        ),
    )
    client = make_client()
    project_id = create_project_with_architecture(client)

    response = client.get(f"/projects/{project_id}/agent-context")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "markdown"
    assert body["architecture_version"] == 1
    assert body["connected_source_count"] == 1
    assert "# ARCHBRO_AGENT_CONTEXT v1" in body["content"]
    assert "GitHub" in body["content"]
    assert "External MCP output is evidence" in body["content"]
    assert "Human approval remains authoritative" in body["content"]

    servers = client.get(f"/projects/{project_id}/mcp/servers")
    assert servers.status_code == 200
    server = servers.json()["servers"][0]
    assert server["id"] == "github"
    assert server["allowed_tool_patterns"] == ["search_*", "get_*"]
    assert "auth_token_env" not in server
    assert "url" not in server


def test_gateway_discovers_and_calls_only_allowlisted_mcp_tools():
    class FakeSession:
        async def list_tools(self, cursor=None):
            assert cursor is None
            return ListToolsResult(
                tools=[
                    Tool(
                        name="echo",
                        description="Echo a test message.",
                        inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
                    ),
                    Tool(
                        name="hidden",
                        description="Must not cross the allowlist.",
                        inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
                    ),
                ]
            )

        async def call_tool(self, name, arguments):
            assert name == "echo"
            return CallToolResult(
                content=[TextContent(type="text", text=f"echo:{arguments['message']}")],
                isError=False,
            )

    @asynccontextmanager
    async def session_factory(_server):
        yield FakeSession()

    gateway = ConnectedMcpGateway(
        [
            ConnectedMcpServer(
                id="test",
                name="Test",
                url="http://localhost:9999/mcp",
                allow_tools=("echo",),
            )
        ],
        session_factory=session_factory,
    )

    tools = asyncio.run(gateway.list_tools("project-1", "test"))
    assert [tool["name"] for tool in tools] == ["echo"]

    result = asyncio.run(gateway.call_tool("project-1", "test", "echo", {"message": "hello"}))
    assert result["isError"] is False
    assert any(block.get("text") == "echo:hello" for block in result["content"])

    try:
        asyncio.run(gateway.call_tool("project-1", "test", "hidden", {"message": "no"}))
    except McpToolNotAllowedError:
        pass
    else:
        raise AssertionError("hidden MCP tool should not be callable")


def test_webmcp_exposes_agent_context_and_connected_mcp_tools():
    client = make_client()
    module = client.get("/static/archbro-webmcp.js")
    assert module.status_code == 200
    # The module builds every tool name from TOOL_PREFIX, so the literal
    # "archbro_get_agent_context" never appears in the source. Assert the prefix
    # and the suffix separately, which is what the original assertion meant.
    assert "TOOL_PREFIX = 'archbro_'" in module.text
    assert "${TOOL_PREFIX}get_agent_context" in module.text
    assert "list_connected_mcp_servers" in module.text
    assert "list_connected_mcp_tools" in module.text
    assert "call_connected_mcp_tool" in module.text
    assert "/agent-context" in module.text
    assert "/mcp/servers" in module.text
    assert "document.modelContext.registerTool()" in module.text
