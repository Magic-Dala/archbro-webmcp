from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.contracts import (
    Architecture,
    GitHubChangePayload,
    Project,
    ProjectActivity,
    ProjectEvent,
    ProjectEventSource,
    ProjectEventType,
    utcnow,
)
from archbro.backend.core.observation import ObservationInProgressError
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.llm.provider import GoalConversationMessage, GoalDraft, ModelProvider


class CreateProjectRequest(BaseModel):
    name: str
    goal: str
    description: str = ""

    @field_validator("name", "goal")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


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


def build_router(
    repository: ProjectRepositoryPort,
    provider: ModelProvider,
    *,
    goal_request_timeout_seconds: float = 30.0,
) -> APIRouter:
    """Build Jim-owned product/API routes against injected platform dependencies."""

    if goal_request_timeout_seconds <= 0:
        raise ValueError("goal_request_timeout_seconds must be greater than zero")

    orchestrator = AgentOrchestrator(repository, provider)
    executor = ActionExecutor(repository)
    router = APIRouter()

    @router.post("/onboarding/goal", response_model=GoalDraft)
    async def draft_goal(request: GoalDraftRequest) -> GoalDraft:
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
    async def create_project(request: CreateProjectRequest) -> Project:
        project = Project(name=request.name, goal=request.goal, description=request.description.strip())
        repository.save_project(project)
        repository.save_architecture(project.id, Architecture())
        return project

    @router.get("/projects", response_model=list[Project])
    async def list_projects() -> list[Project]:
        return repository.list_projects()

    @router.get("/projects/{project_id}", response_model=Project)
    async def get_project(project_id: str) -> Project:
        try:
            return repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")

    @router.patch("/projects/{project_id}", response_model=Project)
    async def update_project(project_id: str, request: UpdateProjectRequest) -> Project:
        try:
            project = repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")

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
    async def delete_project(project_id: str):
        if not repository.delete_project(project_id):
            raise HTTPException(status_code=404, detail="project not found")
        return None

    @router.post("/projects/{project_id}/events")
    async def post_event(project_id: str, request: EventRequest):
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

    @router.get("/projects/{project_id}/events")
    async def list_events(project_id: str):
        try:
            repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        return repository.list_events(project_id)

    @router.get("/projects/{project_id}/agent-runs")
    async def list_agent_runs(project_id: str):
        try:
            repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        return repository.list_agent_runs(project_id)

    @router.get("/projects/{project_id}/activity", response_model=ProjectActivity)
    async def get_activity(project_id: str) -> ProjectActivity:
        try:
            repository.get_project(project_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        return ProjectActivity(
            events=repository.list_events(project_id),
            agent_runs=repository.list_agent_runs(project_id),
        )

    @router.get("/projects/{project_id}/tasks")
    async def list_tasks(project_id: str):
        return repository.list_tasks(project_id)

    @router.get("/projects/{project_id}/architecture")
    async def get_architecture(project_id: str):
        return repository.get_architecture(project_id)

    @router.get("/projects/{project_id}/architecture/proposals")
    async def list_proposals(project_id: str):
        return repository.list_proposals(project_id)

    @router.post("/projects/{project_id}/architecture/proposals/{proposal_id}/accept")
    async def accept_proposal(project_id: str, proposal_id: str):
        try:
            return executor.accept_proposal(project_id, proposal_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/projects/{project_id}/architecture/proposals/{proposal_id}/reject")
    async def reject_proposal(project_id: str, proposal_id: str):
        try:
            return executor.reject_proposal(project_id, proposal_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return router
