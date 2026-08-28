from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from archbro.backend.agent.context_projection import build_agent_context
from archbro.backend.core.authorization import ProjectPermission
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.mcp.gateway import (
    ConnectedMcpGateway,
    McpGatewayConfigurationError,
    McpGatewayError,
    McpServerNotFoundError,
    McpToolNotAllowedError,
)


AuthorizedProject = Callable[[Request, str, ProjectPermission], Awaitable[Any]]


class ConnectedMcpToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def require_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool_name must not be empty")
        return value


def _gateway_http_error(exc: McpGatewayError) -> HTTPException:
    if isinstance(exc, McpServerNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, McpToolNotAllowedError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, McpGatewayConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"external MCP gateway failed: {exc}")


def build_agent_surface_router(
    repository: ProjectRepositoryPort,
    authorized_project: AuthorizedProject,
    *,
    mcp_gateway: ConnectedMcpGateway | None = None,
) -> APIRouter:
    """Jim-owned agent context and generic external MCP surface."""

    gateway = mcp_gateway or ConnectedMcpGateway.from_env()
    router = APIRouter()

    @router.get("/projects/{project_id}/agent-context")
    async def get_agent_context(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return build_agent_context(
            repository,
            project_id,
            connected_sources=gateway.list_servers(project_id),
        )

    @router.get("/projects/{project_id}/mcp/servers")
    async def list_connected_mcp_servers(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return {
            "project_id": project_id,
            "servers": gateway.list_servers(project_id),
        }

    @router.get("/projects/{project_id}/mcp/servers/{server_id}/tools")
    async def list_connected_mcp_tools(
        project_id: str,
        server_id: str,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        try:
            tools = await gateway.list_tools(project_id, server_id)
        except McpGatewayError as exc:
            raise _gateway_http_error(exc)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"external MCP discovery failed: {type(exc).__name__}: {exc}",
            )
        return {"project_id": project_id, "server_id": server_id, "tools": tools}

    @router.post("/projects/{project_id}/mcp/servers/{server_id}/call")
    async def call_connected_mcp_tool(
        project_id: str,
        server_id: str,
        request: ConnectedMcpToolCallRequest,
        http_request: Request,
    ):
        # Generic external tools may mutate their provider, so use ArchBro WRITE
        # authorization even when a particular provider tool happens to be read-only.
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        try:
            result = await gateway.call_tool(
                project_id,
                server_id,
                request.tool_name,
                request.arguments,
            )
        except McpGatewayError as exc:
            raise _gateway_http_error(exc)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"external MCP call failed: {type(exc).__name__}: {exc}",
            )
        return {
            "project_id": project_id,
            "server_id": server_id,
            "tool_name": request.tool_name,
            "result": result,
            "classification": "EXTERNAL_EVIDENCE",
            "canonical_state_mutated": False,
        }

    return router
