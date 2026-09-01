from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from archbro.backend.agent.code_architecture import (
    CodeArchitectureSnapshotRequest,
    build_code_architecture_snapshot,
)
from archbro.backend.agent.context_projection import build_agent_context
from archbro.backend.agent.node_context import (
    ArchitectureNodeNotFoundError,
    StaleArchitectureVersionError,
    build_node_context,
    find_architecture_path,
)
from archbro.backend.core.authorization import ProjectPermission
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    ProjectEvent,
    ProjectEventSource,
    ProjectEventType,
    TaskOwner,
    TaskProposal,
    TaskSource,
)
from archbro.backend.core.repository import (
    ConcurrentStateError,
    IdempotencyConflictError,
    ProjectRepositoryPort,
)
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


class CreateAgentTaskRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    title: str
    description: str = ""
    owner: TaskOwner = TaskOwner.UNASSIGNED
    related_component: str | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=40)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("request_id", "title")
    @classmethod
    def require_nonempty_task_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task request_id and title must not be empty")
        return value

    @field_validator("description")
    @classmethod
    def normalize_task_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("related_component")
    @classmethod
    def normalize_related_component(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("dependencies", "acceptance_criteria")
    @classmethod
    def normalize_task_lists(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values if item.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("task list values must be unique")
        return normalized


class UpdateAgentTaskStatusRequest(BaseModel):
    status: Literal["IN_PROGRESS", "DONE"]


class RecordProjectObservationRequest(BaseModel):
    summary: str
    evidence: list[str] = Field(default_factory=list, max_length=50)
    related_components: list[str] = Field(default_factory=list, max_length=20)
    related_task_id: str | None = None

    @field_validator("summary")
    @classmethod
    def require_observation_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("observation summary must not be empty")
        return value

    @field_validator("evidence", "related_components")
    @classmethod
    def normalize_observation_lists(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values if item.strip()]
        return list(dict.fromkeys(normalized))

    @field_validator("related_task_id")
    @classmethod
    def normalize_related_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def require_evidence_or_project_reference(self) -> "RecordProjectObservationRequest":
        if not self.evidence and not self.related_components and not self.related_task_id:
            raise ValueError(
                "project observation requires evidence, a related component, or a related task"
            )
        return self


def _gateway_http_error(exc: McpGatewayError) -> HTTPException:
    if isinstance(exc, McpServerNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, McpToolNotAllowedError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, McpGatewayConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"external MCP gateway failed: {exc}")


def _architecture_query_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ArchitectureNodeNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"error": "architecture_node_not_found", "node_id": exc.node_id},
        )
    if isinstance(exc, StaleArchitectureVersionError):
        return HTTPException(
            status_code=409,
            detail={
                "error": "stale_architecture_version",
                "expected_architecture_version": exc.expected,
                "current_architecture_version": exc.current,
            },
        )
    raise exc


def _architecture_component_ids(architecture: Any) -> set[str]:
    result: set[str] = set()

    def collect(nodes: list[Any]) -> None:
        for node in nodes:
            result.add(node.id)
            collect(node.children)

    collect(architecture.components)
    return result


def build_agent_surface_router(
    repository: ProjectRepositoryPort,
    authorized_project: AuthorizedProject,
    *,
    mcp_gateway: ConnectedMcpGateway | None = None,
) -> APIRouter:
    """Jim-owned agent context and generic external MCP surface."""

    gateway = mcp_gateway or ConnectedMcpGateway.from_env()
    executor = ActionExecutor(repository)
    router = APIRouter()

    @router.get("/projects/{project_id}/agent-context")
    async def get_agent_context(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return build_agent_context(
            repository,
            project_id,
            connected_sources=gateway.list_servers(project_id),
        )

    @router.post("/projects/{project_id}/tasks")
    async def create_agent_task(
        project_id: str,
        request: CreateAgentTaskRequest,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        architecture = repository.get_architecture(project_id)
        if architecture.version == 0:
            raise HTTPException(
                status_code=409,
                detail="initial architecture must exist before creating follow-up tasks",
            )

        component_ids = _architecture_component_ids(architecture)
        if request.related_component and request.related_component not in component_ids:
            raise HTTPException(
                status_code=422,
                detail=f"unknown architecture component id: {request.related_component}",
            )

        existing_tasks = {task.id: task for task in repository.list_tasks(project_id)}
        unknown_dependencies = sorted(set(request.dependencies).difference(existing_tasks))
        if unknown_dependencies:
            raise HTTPException(
                status_code=422,
                detail=f"task dependencies do not belong to project: {unknown_dependencies}",
            )

        task_proposal = TaskProposal(
            title=request.title,
            description=request.description,
            owner=request.owner,
            source=TaskSource.AGENT,
            related_component=request.related_component,
            dependencies=request.dependencies,
            acceptance_criteria=request.acceptance_criteria,
        )
        request_fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"request_id"}).encode("utf-8")
        ).hexdigest()
        action = AgentAction(
            type=AgentActionType.CREATE_TASK,
            payload={"task": task_proposal.model_dump(mode="json")},
        )
        idempotent_replay = False
        try:
            plan = executor.build_plan(project_id, [action])
            if len(plan.tasks) != 1:
                raise ValueError("create task did not materialize exactly one task")
            task = plan.tasks[0]
            event = ProjectEvent(
                project_id=project_id,
                type=ProjectEventType.MANUAL_NOTE,
                source=ProjectEventSource.SYSTEM,
                source_event_id=f"semantic-task-create:{request.request_id}",
                payload={
                    "external_source": "WEBMCP_AGENT",
                    "intent": "CREATE_TASK",
                    "request_fingerprint": request_fingerprint,
                    "summary": f"Created task: {task.title}",
                    "task_id": task.id,
                    "related_component": task.related_component,
                },
            )
            canonical_event = repository.commit_event_actions(
                event=event,
                project=plan.project,
                architecture=plan.architecture,
                tasks=plan.tasks,
                proposals=plan.proposals,
                notes=plan.notes,
                expected_project_updated_at=plan.expected_project_updated_at,
                expected_architecture_version=plan.expected_architecture_version,
                expected_task_updated_at=plan.expected_task_updated_at,
            )
            if canonical_event.id != event.id:
                canonical_task_id = canonical_event.payload.get("task_id")
                if not isinstance(canonical_task_id, str) or not canonical_task_id:
                    raise ValueError("idempotent create event is missing canonical task_id")
                task = repository.get_task(canonical_task_id)
                event = canonical_event
                idempotent_replay = True
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ConcurrentStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "task": task,
            "event_id": event.id,
            "idempotent_replay": idempotent_replay,
            "built_in_model_called": False,
        }

    @router.patch("/projects/{project_id}/tasks/{task_id}/status")
    async def update_agent_task_status(
        project_id: str,
        task_id: str,
        request: UpdateAgentTaskStatusRequest,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        project_tasks = {task.id: task for task in repository.list_tasks(project_id)}
        task = project_tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found for project")
        if request.status == "IN_PROGRESS" and task.status.value != "TODO":
            raise HTTPException(status_code=409, detail="task must be TODO before starting")
        if request.status == "DONE" and task.status.value != "IN_PROGRESS":
            raise HTTPException(status_code=409, detail="task must be IN_PROGRESS before completion")

        action = AgentAction(
            type=AgentActionType.UPDATE_TASK,
            payload={"task_id": task_id, "changes": {"status": request.status}},
        )
        try:
            plan = executor.build_plan(project_id, [action])
            if len(plan.tasks) != 1:
                raise ValueError("task status update did not materialize exactly one task")
            updated = plan.tasks[0]
            event = ProjectEvent(
                project_id=project_id,
                type=ProjectEventType.TASK_UPDATED,
                source=ProjectEventSource.SYSTEM,
                payload={
                    "external_source": "WEBMCP_AGENT",
                    "intent": "TASK_STATUS_TRANSITION",
                    "task_id": task_id,
                    "title": updated.title,
                    "status": updated.status.value,
                    "summary": f'Task "{updated.title}" changed to {updated.status.value}.',
                },
            )
            repository.commit_event_actions(
                event=event,
                project=plan.project,
                architecture=plan.architecture,
                tasks=plan.tasks,
                proposals=plan.proposals,
                notes=plan.notes,
                expected_project_updated_at=plan.expected_project_updated_at,
                expected_architecture_version=plan.expected_architecture_version,
                expected_task_updated_at=plan.expected_task_updated_at,
            )
        except ConcurrentStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "task": updated,
            "event_id": event.id,
            "built_in_model_called": False,
        }

    @router.post("/projects/{project_id}/observations")
    async def record_project_observation(
        project_id: str,
        request: RecordProjectObservationRequest,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        repository.get_project(project_id)
        architecture = repository.get_architecture(project_id)
        component_ids = _architecture_component_ids(architecture)
        unknown_components = sorted(set(request.related_components).difference(component_ids))
        if unknown_components:
            raise HTTPException(
                status_code=422,
                detail=f"unknown architecture component ids: {unknown_components}",
            )
        if request.related_task_id:
            task_ids = {task.id for task in repository.list_tasks(project_id)}
            if request.related_task_id not in task_ids:
                raise HTTPException(status_code=422, detail="related task does not belong to project")

        event = ProjectEvent(
            project_id=project_id,
            type=ProjectEventType.MANUAL_NOTE,
            source=ProjectEventSource.SYSTEM,
            payload={
                "external_source": "WEBMCP_AGENT",
                "intent": "PROJECT_OBSERVATION",
                "summary": request.summary,
                "evidence": request.evidence,
                "related_components": request.related_components,
                "related_task_id": request.related_task_id,
            },
        )
        repository.save_event(event)
        return {
            "event": event,
            "canonical_architecture_mutated": False,
            "built_in_model_called": False,
        }

    @router.post("/projects/{project_id}/code-architecture/snapshot")
    async def build_repository_code_architecture(
        project_id: str,
        request: CodeArchitectureSnapshotRequest,
        http_request: Request,
    ):
        # This is a derived evidence projection, not a canonical architecture write.
        # READ authorization is sufficient because no provider or ArchBro state is mutated.
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        repository.get_project(project_id)
        return build_code_architecture_snapshot(project_id, request)

    @router.post("/projects/{project_id}/code-architecture/snapshots")
    async def publish_repository_code_architecture(
        project_id: str,
        request: CodeArchitectureSnapshotRequest,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        repository.get_project(project_id)
        canonical_request = request.model_dump_json()
        digest = hashlib.sha256(f"{project_id}\x1f{canonical_request}".encode("utf-8")).hexdigest()
        event = ProjectEvent(
            id=f"event_code_architecture_{digest[:32]}",
            project_id=project_id,
            type=ProjectEventType.CODE_ARCHITECTURE_SNAPSHOT,
            source=ProjectEventSource.SYSTEM,
            source_event_id=f"code-architecture:{request.repository}@{request.revision}:{digest[:24]}",
            payload={
                "artifact_type": "CODE_ARCHITECTURE_SNAPSHOT",
                "request": request.model_dump(mode="json"),
            },
        )
        repository.save_event(event)
        snapshot = build_code_architecture_snapshot(project_id, request)
        return {
            **snapshot,
            "derived_artifact_persisted": True,
            "event_id": event.id,
            "published_at": event.timestamp.isoformat(),
        }

    @router.get("/projects/{project_id}/code-architecture/latest")
    async def get_latest_repository_code_architecture(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        repository.get_project(project_id)
        event = repository.get_latest_event_by_type(
            project_id,
            ProjectEventType.CODE_ARCHITECTURE_SNAPSHOT,
        )
        if event is not None:
            raw_request = event.payload.get("request")
            if isinstance(raw_request, dict):
                try:
                    snapshot_request = CodeArchitectureSnapshotRequest.model_validate(raw_request)
                except ValueError:
                    snapshot_request = None
                if snapshot_request is not None:
                    snapshot = build_code_architecture_snapshot(project_id, snapshot_request)
                    return {
                        **snapshot,
                        "derived_artifact_persisted": True,
                        "event_id": event.id,
                        "published_at": event.timestamp.isoformat(),
                    }
        return Response(status_code=204)

    @router.get("/projects/{project_id}/architecture/nodes/{node_id}/context")
    async def get_architecture_node_context(
        project_id: str,
        node_id: str,
        http_request: Request,
        direction: Literal["upstream", "downstream", "both"] = "both",
        max_hops: int = Query(default=1, ge=1, le=8),
        max_results: int = Query(default=20, ge=1, le=40),
        expected_architecture_version: int | None = None,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        architecture = repository.get_architecture(project_id)
        try:
            return build_node_context(
                architecture,
                project_id,
                node_id,
                direction=direction,
                max_hops=max_hops,
                max_results=max_results,
                expected_architecture_version=expected_architecture_version,
            )
        except (ArchitectureNodeNotFoundError, StaleArchitectureVersionError) as exc:
            raise _architecture_query_http_error(exc)

    @router.get("/projects/{project_id}/architecture/path")
    async def get_architecture_path(
        project_id: str,
        http_request: Request,
        source_id: str,
        target_id: str,
        max_hops: int = Query(default=8, ge=0, le=8),
        expected_architecture_version: int | None = None,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        architecture = repository.get_architecture(project_id)
        try:
            return find_architecture_path(
                architecture,
                project_id,
                source_id,
                target_id,
                max_hops=max_hops,
                expected_architecture_version=expected_architecture_version,
            )
        except (ArchitectureNodeNotFoundError, StaleArchitectureVersionError) as exc:
            raise _architecture_query_http_error(exc)

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
