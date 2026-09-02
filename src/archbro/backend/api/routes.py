from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.api.agent_surface import build_agent_surface_router
from archbro.backend.api.provider_connections import build_provider_mcp_router
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.authorization import (
    AuthenticationError,
    IdentityProviderUnavailableError,
    InvalidCredentialsError,
    PrincipalProvider,
    ProjectAuthorizationError,
    ProjectAuthorizer,
    ProjectPermission,
    local_development_principal,
)
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Project,
    ProjectActivity,
    ProjectEvent,
    ProjectEventSource,
    ProjectEventType,
    TaskProposal,
    utcnow,
)
from archbro.backend.core.observation import ObservationInProgressError
from archbro.backend.core.diagram import (
    ArchitectureNodeNotFoundError,
    map_edge_ids,
    project_scoped_diagram,
)
from archbro.backend.core.diagram_layout import layout_diagram
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.llm.provider import GoalConversationMessage, GoalDraft, ModelProvider


class CreateProjectRequest(BaseModel):
    name: str
    goal: str
    description: str = ""
    team_id: str | None = None

    @field_validator("name", "goal")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("team_id")
    @classmethod
    def normalize_team_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    goal: str | None = None
    description: str | None = None

    @field_validator("name", "goal")
    @classmethod
    def require_non_empty_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def require_a_change(self) -> "UpdateProjectRequest":
        if self.name is None and self.goal is None and self.description is None:
            raise ValueError("at least one project field is required")
        return self


class GoalDraftRequest(BaseModel):
    messages: list[GoalConversationMessage] = []
    current_goal: str = ""

    @model_validator(mode="after")
    def require_goal_or_user_message(self) -> "GoalDraftRequest":
        has_goal = bool(self.current_goal.strip())
        has_ask = any(message.role == "user" and message.content.strip() for message in self.messages)
        if not has_goal and not has_ask:
            raise ValueError("a current Goal or at least one user Ask message is required")
        return self


class EventRequest(BaseModel):
    type: ProjectEventType
    source: ProjectEventSource = ProjectEventSource.HUMAN
    source_event_id: str | None = None
    occurred_at: datetime | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_provider_contract(self) -> "EventRequest":
        if self.source_event_id is not None:
            self.source_event_id = self.source_event_id.strip() or None
        if self.source in {ProjectEventSource.GITHUB, ProjectEventSource.SYSTEM}:
            raise ValueError(
                "trusted event sources must be supplied by a verified server-side integration"
            )
        if self.type == ProjectEventType.GITHUB_CHANGE:
            raise ValueError(
                "GITHUB_CHANGE must enter through the verified GitHub integration boundary"
            )
        return self


class InitialScopePlanningEvaluation(BaseModel):
    scope_component_id: str
    decomposition: Literal["EXPANDED", "JUSTIFIED_LEAF"]
    child_ids: list[str] = Field(default_factory=list, max_length=12)
    leaf_reason: str | None = Field(default=None, max_length=280)

    @field_validator("scope_component_id")
    @classmethod
    def normalize_scope_component_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("child_ids")
    @classmethod
    def normalize_child_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("child ids must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("child ids must be unique")
        return normalized

    @field_validator("leaf_reason")
    @classmethod
    def normalize_leaf_reason(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_decomposition(self) -> "InitialScopePlanningEvaluation":
        if self.decomposition == "EXPANDED":
            if not self.child_ids:
                raise ValueError("EXPANDED scope must name at least one immediate child")
            if self.leaf_reason:
                raise ValueError("EXPANDED scope must not provide leaf_reason")
        else:
            if self.child_ids:
                raise ValueError("JUSTIFIED_LEAF scope must not name child ids")
            if len(self.leaf_reason or "") < 24:
                raise ValueError("JUSTIFIED_LEAF requires a specific leaf_reason of at least 24 characters")
        return self


class InitialArchitecturePlanningTrace(BaseModel):
    system_map_root_ids: list[str] = Field(min_length=1, max_length=8)
    scope_evaluations: list[InitialScopePlanningEvaluation] = Field(min_length=1, max_length=80)
    reconciled: bool

    @field_validator("system_map_root_ids")
    @classmethod
    def normalize_system_map_root_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("system map root ids must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("system map root ids must be unique")
        return normalized

    @model_validator(mode="after")
    def require_reconcile_phase(self) -> "InitialArchitecturePlanningTrace":
        if self.reconciled is not True:
            raise ValueError("outside-in initial planning must complete RECONCILE before commit")
        return self


class InteractiveInitialArchitectureRequest(BaseModel):
    architecture: Architecture
    tasks: list[TaskProposal]
    reasoning: str
    planning_trace: InitialArchitecturePlanningTrace

    @field_validator("reasoning")
    @classmethod
    def require_bootstrap_reasoning(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_initial_architecture(self) -> "InteractiveInitialArchitectureRequest":
        if self.architecture.version != 1:
            raise ValueError("interactive initial architecture must be version 1")
        if not self.tasks:
            raise ValueError("at least one initial task is required")
        component_ids = self.architecture.component_ids()
        unknown_task_components = sorted({
            task.related_component
            for task in self.tasks
            if task.related_component and task.related_component not in component_ids
        })
        if unknown_task_components:
            raise ValueError(f"initial tasks reference unknown architecture component ids: {unknown_task_components}")
        root_ids = [component.id for component in self.architecture.components]
        leaf_roots = [component.id for component in self.architecture.components if not component.children]
        if leaf_roots:
            raise ValueError(
                "WebMCP SYSTEM_MAP roots must be expanded architecture boundaries; "
                f"atomic components belong below a root: {leaf_roots}"
            )
        if self.planning_trace.system_map_root_ids != root_ids:
            raise ValueError(
                "planning_trace.system_map_root_ids must exactly match final architecture roots in order"
            )

        def preorder_components(components) -> list:
            result: list = []
            for component in components:
                result.append(component)
                result.extend(preorder_components(component.children))
            return result

        planned_components = preorder_components(self.architecture.components)
        evaluation_ids = [evaluation.scope_component_id for evaluation in self.planning_trace.scope_evaluations]
        expected_ids = [component.id for component in planned_components]
        if evaluation_ids != expected_ids:
            raise ValueError(
                "planning_trace.scope_evaluations must cover every canonical component exactly once in preorder"
            )
        for component, evaluation in zip(planned_components, self.planning_trace.scope_evaluations, strict=True):
            expected_child_ids = [child.id for child in component.children]
            if expected_child_ids:
                if evaluation.decomposition != "EXPANDED":
                    raise ValueError(f"planning_trace scope {component.id} has children and must be EXPANDED")
                if evaluation.child_ids != expected_child_ids:
                    raise ValueError(
                        "planning_trace child ids must exactly match immediate final children for scope "
                        f"{component.id}"
                    )
            elif evaluation.decomposition != "JUSTIFIED_LEAF":
                raise ValueError(f"planning_trace scope {component.id} has no children and must be JUSTIFIED_LEAF")
        return self


class AgentRecommendationRequest(BaseModel):
    recommendation: ArchitectureOption
    expected_architecture_version: int = Field(ge=0)
    reasoning: str
    evidence: list[str]
    observed_change: str
    affected_components: list[str] = []
    proposed_changes: list[dict[str, Any]] = []
    impact: str = ""

    @field_validator("reasoning", "observed_change")
    @classmethod
    def require_recommendation_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_recommendation_payload(self) -> "AgentRecommendationRequest":
        self.evidence = [item.strip() for item in self.evidence if item.strip()]
        self.affected_components = [item.strip() for item in self.affected_components if item.strip()]
        self.impact = self.impact.strip()
        if not self.evidence:
            raise ValueError("at least one evidence item is required")
        if self.recommendation == ArchitectureOption.ACCEPT_PROPOSED_CHANGE:
            if not self.proposed_changes:
                raise ValueError("proposed_changes are required when recommending an architecture change")
            if not self.impact:
                raise ValueError("impact is required when recommending an architecture change")
        return self


def build_router(
    repository: ProjectRepositoryPort,
    provider: ModelProvider,
    *,
    goal_request_timeout_seconds: float = 30.0,
    principal_provider: PrincipalProvider | None = None,
) -> APIRouter:
    """Build Jim-owned product/API routes against injected platform dependencies."""

    if goal_request_timeout_seconds <= 0:
        raise ValueError("goal_request_timeout_seconds must be greater than zero")

    orchestrator = AgentOrchestrator(repository, provider)
    executor = ActionExecutor(repository)
    authorizer = ProjectAuthorizer()
    router = APIRouter()

    def authentication_error(detail: str) -> HTTPException:
        return HTTPException(
            status_code=401,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def principal_for(http_request: Request):
        if principal_provider is None:
            return local_development_principal()

        authorization = http_request.headers.get("Authorization", "")
        scheme, separator, credentials = authorization.partition(" ")
        token = credentials.strip()
        if not separator or scheme.lower() != "bearer" or not token:
            raise authentication_error("missing or invalid bearer token")

        try:
            return await principal_provider(token)
        except IdentityProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc) or "identity provider unavailable")
        except InvalidCredentialsError as exc:
            raise authentication_error(str(exc) or "invalid bearer token")
        except AuthenticationError as exc:
            raise authentication_error(str(exc) or "authentication failed")

    async def authorized_project(
        http_request: Request,
        project_id: str,
        permission: ProjectPermission,
    ) -> Project:
        principal = await principal_for(http_request)
        try:
            project = repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        try:
            authorizer.require(principal, project, permission)
        except ProjectAuthorizationError as exc:
            # Do not reveal whether an opaque project id exists to a principal
            # that has no read access at all. Principals that can already read
            # the project still receive 403 for a stronger denied permission.
            if not authorizer.can_read(principal, project):
                raise HTTPException(status_code=404, detail="project not found")
            raise HTTPException(status_code=403, detail=str(exc))
        return project

    @router.post("/onboarding/goal", response_model=GoalDraft)
    async def draft_goal(request: GoalDraftRequest, http_request: Request) -> GoalDraft:
        # Goal drafting can invoke the configured model provider, so production
        # authentication must precede any provider call just like project APIs.
        await principal_for(http_request)
        try:
            return await asyncio.wait_for(
                provider.draft_goal(messages=request.messages, current_goal=request.current_goal),
                timeout=goal_request_timeout_seconds,
            )
        except TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f"Goal drafting exceeded {goal_request_timeout_seconds:g}s. "
                    "Your Goal and Ask are preserved; retry the Ask."
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}")

    @router.post("/projects", response_model=Project)
    async def create_project(request: CreateProjectRequest, http_request: Request) -> Project:
        principal = await principal_for(http_request)
        if request.team_id and request.team_id not in principal.team_ids and not principal.local_development:
            raise HTTPException(status_code=403, detail="cannot create a project for an untrusted team")
        project = Project(
            name=request.name,
            goal=request.goal,
            description=request.description.strip(),
            owner_user_id=principal.user_id,
            team_id=request.team_id,
        )
        repository.save_project(project)
        repository.save_architecture(project.id, Architecture())
        return project

    @router.get("/projects", response_model=list[Project])
    async def list_projects(http_request: Request) -> list[Project]:
        principal = await principal_for(http_request)
        return [project for project in repository.list_projects() if authorizer.can_read(principal, project)]

    @router.get("/projects/{project_id}", response_model=Project)
    async def get_project(project_id: str, http_request: Request) -> Project:
        return await authorized_project(http_request, project_id, ProjectPermission.READ)

    @router.patch("/projects/{project_id}", response_model=Project)
    async def update_project(project_id: str, request: UpdateProjectRequest, http_request: Request) -> Project:
        project = await authorized_project(http_request, project_id, ProjectPermission.WRITE)

        architecture = repository.get_architecture(project_id)
        if request.goal is not None and request.goal != project.goal and architecture.version > 0:
            raise HTTPException(
                status_code=409,
                detail="Goal changes after Architecture v1 must go through the Agent so architecture changes remain reviewable.",
            )
        changes = request.model_dump(exclude_unset=True)
        if "description" in changes:
            changes["description"] = (changes["description"] or "").strip()
        updated = project.model_copy(update={**changes, "updated_at": utcnow()})
        repository.save_project(updated)
        return updated

    @router.delete("/projects/{project_id}", status_code=204)
    async def delete_project(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.MANAGE)
        if not repository.delete_project(project_id):
            raise HTTPException(status_code=404, detail="project not found")
        return None

    @router.post("/projects/{project_id}/events")
    async def post_event(project_id: str, request: EventRequest, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        event = ProjectEvent(
            project_id=project_id,
            type=request.type,
            source=request.source,
            source_event_id=request.source_event_id,
            occurred_at=request.occurred_at,
            payload=request.payload,
        )
        try:
            return await orchestrator.observe_event(event)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        except ObservationInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/projects/{project_id}/events", response_model=list[ProjectEvent])
    async def list_events(
        project_id: str,
        http_request: Request,
        limit: int = Query(default=20, ge=1, le=50),
    ) -> list[ProjectEvent]:
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return repository.list_events(project_id, limit=limit)

    @router.get("/projects/{project_id}/agent-runs")
    async def list_agent_runs(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return repository.list_agent_runs(project_id)

    @router.get("/projects/{project_id}/activity", response_model=ProjectActivity)
    async def get_activity(project_id: str, http_request: Request) -> ProjectActivity:
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return ProjectActivity(
            events=repository.list_events(project_id),
            agent_runs=repository.list_agent_runs(project_id),
        )

    @router.get("/projects/{project_id}/tasks")
    async def list_tasks(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return repository.list_tasks(project_id)

    @router.get("/projects/{project_id}/architecture")
    async def get_architecture(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return repository.get_architecture(project_id)

    @router.get("/projects/{project_id}/architecture/diagram")
    async def get_architecture_diagram(
        project_id: str,
        http_request: Request,
        scope: str | None = Query(default=None),
        expected_architecture_version: int | None = Query(default=None, ge=0),
        reading_mode: Literal["MAP", "READ", "FULL"] = Query(default="FULL"),
    ):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        architecture = repository.get_architecture(project_id)
        if (
            expected_architecture_version is not None
            and expected_architecture_version != architecture.version
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_architecture_version",
                    "expected_architecture_version": expected_architecture_version,
                    "current_architecture_version": architecture.version,
                },
            )
        try:
            projection = project_scoped_diagram(
                architecture,
                scope_component_id=scope,
                tasks=repository.list_tasks(project_id),
                proposals=repository.list_proposals(project_id),
            )
        except ArchitectureNodeNotFoundError:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "architecture_node_not_found",
                    "component_id": scope,
                },
            )
        full_diagram = projection.diagram
        route_edge_ids = map_edge_ids(full_diagram) if reading_mode == "MAP" else None
        positioned_graph = layout_diagram(full_diagram, route_edge_ids=route_edge_ids)
        diagram = (
            full_diagram.model_copy(
                update={"edges": [edge for edge in full_diagram.edges if edge.id in route_edge_ids]}
            )
            if route_edge_ids is not None
            else full_diagram
        )
        return {
            "schema": projection.schema,
            "project_id": project_id,
            "architecture_version": projection.architecture_version,
            "scope": projection.scope.model_dump(mode="json"),
            "diagram": diagram.model_dump(mode="json"),
            "positioned_graph": asdict(positioned_graph),
        }

    @router.get("/projects/{project_id}/architecture/proposals")
    async def list_proposals(project_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.READ)
        return repository.list_proposals(project_id)

    @router.post("/projects/{project_id}/interactive-initial-architecture")
    async def submit_interactive_initial_architecture(
        project_id: str,
        request: InteractiveInitialArchitectureRequest,
        http_request: Request,
    ):
        project = await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        try:
            current = repository.get_architecture(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        if current.version != 0 or project.architecture_version != 0:
            raise HTTPException(status_code=409, detail="initial architecture already exists")

        event = ProjectEvent(
            project_id=project_id,
            type=ProjectEventType.MANUAL_NOTE,
            source=ProjectEventSource.SYSTEM,
            payload={
                "external_source": "WEBMCP_AGENT",
                "intent": "INITIAL_ARCHITECTURE",
                "summary": request.reasoning,
                "provider": "webmcp-agent",
                "planning_trace": request.planning_trace.model_dump(mode="json", exclude_none=True),
            },
        )
        actions = [
            AgentAction(
                type=AgentActionType.ADD_PROJECT_NOTE,
                payload={"note": "INITIAL_ARCHITECTURE:" + request.architecture.model_dump_json()},
            ),
            *[
                AgentAction(
                    type=AgentActionType.CREATE_TASK,
                    payload={"task": task.model_dump(mode="json")},
                )
                for task in request.tasks
            ],
        ]
        try:
            executor.validate_all(project_id, actions)
            repository.save_event(event)
            executor.apply(project_id, actions)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "provider": "webmcp-agent",
            "model": "external-interactive",
            "event_id": event.id,
            "architecture": repository.get_architecture(project_id),
            "tasks": repository.list_tasks(project_id),
            "summary": request.reasoning,
        }

    @router.post("/projects/{project_id}/agent-recommendations")
    async def submit_agent_recommendation(
        project_id: str,
        request: AgentRecommendationRequest,
        http_request: Request,
    ):
        await authorized_project(http_request, project_id, ProjectPermission.WRITE)
        try:
            architecture = repository.get_architecture(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        if architecture.version == 0:
            raise HTTPException(status_code=409, detail="initial architecture must exist before submitting an agent recommendation")
        if request.expected_architecture_version != architecture.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_architecture_version",
                    "expected_architecture_version": request.expected_architecture_version,
                    "current_architecture_version": architecture.version,
                },
            )

        component_ids: set[str] = set()

        def collect_component_ids(nodes) -> None:
            for node in nodes:
                component_ids.add(node.id)
                collect_component_ids(node.children)

        collect_component_ids(architecture.components)
        referenced_ids = set(request.affected_components)
        for change in request.proposed_changes:
            component_id = str(change.get("component_id", "")).strip()
            if component_id:
                referenced_ids.add(component_id)
        unknown_ids = sorted(referenced_ids.difference(component_ids))
        if unknown_ids:
            raise HTTPException(status_code=422, detail=f"unknown architecture component ids: {unknown_ids}")

        event = ProjectEvent(
            project_id=project_id,
            type=ProjectEventType.MANUAL_NOTE,
            source=ProjectEventSource.SYSTEM,
            payload={
                "external_source": "WEBMCP_AGENT",
                "summary": request.reasoning,
                "recommendation": request.recommendation.value,
                "evidence": request.evidence,
            },
        )

        if request.recommendation == ArchitectureOption.KEEP_CURRENT:
            repository.save_event(event)
            return {
                "provider": "webmcp-agent",
                "model": "external-interactive",
                "event_id": event.id,
                "architecture_review_required": False,
                "proposal": None,
                "summary": request.reasoning,
            }

        proposal = ArchitectureChangeProposal(
            project_id=project_id,
            reason=request.reasoning,
            evidence=request.evidence,
            observed_change=request.observed_change,
            affected_components=request.affected_components,
            proposed_changes=request.proposed_changes,
            impact=request.impact,
            recommended_option=request.recommendation,
        )
        action = AgentAction(
            type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
            payload={"proposal": proposal.model_dump(mode="json")},
        )
        try:
            executor.validate_all(project_id, [action])
            repository.save_event(event)
            proposal_ids = executor.apply(project_id, [action])
            persisted_proposal = repository.get_proposal(proposal_ids[0])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "provider": "webmcp-agent",
            "model": "external-interactive",
            "event_id": event.id,
            "architecture_review_required": True,
            "proposal": persisted_proposal,
            "summary": request.reasoning,
        }

    @router.post("/projects/{project_id}/architecture/proposals/{proposal_id}/accept")
    async def accept_proposal(project_id: str, proposal_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.REVIEW)
        try:
            return executor.accept_proposal(project_id, proposal_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/projects/{project_id}/architecture/proposals/{proposal_id}/reject")
    async def reject_proposal(project_id: str, proposal_id: str, http_request: Request):
        await authorized_project(http_request, project_id, ProjectPermission.REVIEW)
        try:
            return executor.reject_proposal(project_id, proposal_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    router.include_router(build_agent_surface_router(repository, authorized_project))
    router.include_router(build_provider_mcp_router(principal_for))
    return router
