from __future__ import annotations

import sys

import pytest

from archbro.backend.api import provider_connections
from archbro.backend.api.provider_connections import _configured_worker_count
from archbro.backend.mcp.provider_policy import ReadOnlyExternalMcpGateway


def _github_gateway() -> tuple[ReadOnlyExternalMcpGateway, str]:
    gateway = ReadOnlyExternalMcpGateway(timeout_seconds=2)
    connection = gateway.add_bearer_connection(
        provider="github",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        access_token="test-token",
        auth_type="oauth",
    )
    return gateway, connection["id"]


def test_provider_router_uses_local_read_only_gateway_backstop():
    assert provider_connections.ExternalMcpGateway is ReadOnlyExternalMcpGateway


def test_github_tool_list_exposes_only_explicit_read_only_tools(monkeypatch):
    gateway, connection_id = _github_gateway()
    monkeypatch.setattr(
        gateway,
        "_state_list_tools",
        lambda state: [
            {"name": "get_file_contents", "annotations": {"readOnlyHint": True}},
            {"name": "create_or_update_file", "annotations": {"readOnlyHint": False}},
            {"name": "unannotated_tool"},
        ],
    )

    result = gateway.list_tools(connection_id)

    assert [tool["name"] for tool in result["tools"]] == ["get_file_contents"]
    assert result["tool_count"] == 1


def test_github_tool_call_fails_closed_without_explicit_read_only_annotation(monkeypatch):
    gateway, connection_id = _github_gateway()
    dispatched: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_state_list_tools",
        lambda state: [
            {"name": "get_file_contents", "annotations": {"readOnlyHint": True}},
            {"name": "create_or_update_file", "annotations": {"readOnlyHint": False}},
            {"name": "unannotated_tool"},
        ],
    )
    monkeypatch.setattr(
        gateway,
        "_state_call",
        lambda state, name, arguments: dispatched.append(name) or {"ok": True},
    )

    allowed = gateway.call_tool(connection_id, "get_file_contents", {"owner": "o", "repo": "r"})
    assert allowed["external_evidence"] == {"ok": True}
    assert dispatched == ["get_file_contents"]

    with pytest.raises(ValueError, match="not explicitly marked read-only"):
        gateway.call_tool(connection_id, "create_or_update_file", {})
    with pytest.raises(ValueError, match="not explicitly marked read-only"):
        gateway.call_tool(connection_id, "unannotated_tool", {})
    with pytest.raises(ValueError, match="not explicitly marked read-only"):
        gateway.call_tool(connection_id, "unknown_tool", {})

    assert dispatched == ["get_file_contents"]


def test_non_github_provider_keeps_existing_tool_behavior(monkeypatch):
    gateway = ReadOnlyExternalMcpGateway(timeout_seconds=2)
    connection = gateway.add_bearer_connection(
        provider="slack",
        name="Slack",
        url="https://mcp.slack.com/mcp",
        access_token="test-token",
        auth_type="oauth",
    )
    monkeypatch.setattr(
        gateway,
        "_state_call",
        lambda state, name, arguments: {"tool": name},
    )

    result = gateway.call_tool(connection["id"], "search", {})

    assert result["external_evidence"] == {"tool": "search"}


def test_configured_worker_count_includes_uvicorn_cli_workers(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)

    monkeypatch.setattr(sys, "argv", ["uvicorn", "archbro.main:app", "--workers", "3"])
    assert _configured_worker_count() == 3

    monkeypatch.setattr(sys, "argv", ["uvicorn", "archbro.main:app", "--workers=4"])
    assert _configured_worker_count() == 4


def test_configured_worker_count_uses_highest_env_or_cli_value(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "5")
    monkeypatch.setenv("UVICORN_WORKERS", "2")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "archbro.main:app", "--workers", "3"])

    assert _configured_worker_count() == 5
