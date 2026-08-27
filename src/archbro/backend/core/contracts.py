from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from archbro.backend.core.evaluation import DriftEvaluation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


class TaskOwner(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    UNASSIGNED = "UNASSIGNED"


class TaskSource(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    ARCHITECTURE = "ARCHITECTURE"


class ProjectEventType(StrEnum):
    USER_MESSAGE = "USER_MESSAGE"
    TASK_UPDATED = "TASK_UPDATED"
    MANUAL_NOTE = "MANUAL_NOTE"
    GITHUB_CHANGE = "GITHUB_CHANGE"


class ProjectEventSource(StrEnum):
    HUMAN = "HUMAN"
    FRONTEND = "FRONTEND"
    GITHUB = "GITHUB"
    SYSTEM = "SYSTEM"


class GitHubChangeKind(StrEnum):
    PUSH = "PUSH"
    PULL_REQUEST_MERGED = "PULL_REQUEST_MERGED"


class GitHubChangePayload(BaseModel):
    repository: str
    event_kind: GitHubChangeKind
    summary: str
    ref: str | None = None
    commit_sha: str | None = None
    pull_request_number: int | None = Field(default=None, ge=1)
    actor: str | None = None
    title: str | None = None
    changed_files: list[str] = Field(default_factory=list, max_length=200)
    commits: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_github_change(self) -> "GitHubChangePayload":
        self.repository = self.repository.strip()
        self.summary = self.summary.strip()
        self.ref = self.ref.strip() if self.ref else None
        self.commit_sha = self.commit_sha.strip() if self.commit_sha else None
        self.actor = self.actor.strip() if self.actor else None
        self.title = self.title.strip() if self.title else None
        self.changed_files = list(dict.fromkeys(path.strip() for path in self.changed_files if path.strip()))
        self.commits = list(dict.fromkeys(sha.strip() for sha in self.commits if sha.strip()))
        if not self.repository:
            raise ValueError("GitHub change repository must not be empty")
        if not self.summary:
            raise ValueError("GitHub change summary must not be empty")
        if self.event_kind == GitHubChangeKind.PUSH:
            if not self.ref:
                raise ValueError("GitHub PUSH requires ref")
            if not self.commit_sha:
                raise ValueError("GitHub PUSH requires commit_sha")
        if self.event_kind == GitHubChangeKind.PULL_REQUEST_MERGED:
            if self.pull_request_number is None:
                raise ValueError("GitHub PULL_REQUEST_MERGED requires pull_request_number")
            if not self.commit_sha:
                raise ValueError("GitHub PULL_REQUEST_MERGED requires commit_sha")
        return self


class ArchitectureNodeKind(StrEnum):
    SYSTEM = "SYSTEM"
    UI = "UI"
    SERVICE = "SERVICE"
    AGENT = "AGENT"
    TOOL = "TOOL"
    DATA_STORE = "DATA_STORE"
    STATE = "STATE"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class ProposalStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ArchitectureOption(StrEnum):
    KEEP_CURRENT = "KEEP_CURRENT"
    ACCEPT_PROPOSED_CHANGE = "ACCEPT_PROPOSED_CHANGE"


class AgentActionType(StrEnum):
    CREATE_TASK = "CREATE_TASK"
    UPDATE_TASK = "UPDATE_TASK"
    ADD_PROJECT_NOTE = "ADD_PROJECT_NOTE"
    UPDATE_PROJECT_STATUS = "UPDATE_PROJECT_STATUS"
    PROPOSE_ARCHITECTURE_CHANGE = "PROPOSE_ARCHITECTURE_CHANGE"
    NO_ACTION = "NO_ACTION"


class Component(BaseModel):
    id: str
    name: str
    type: str
    responsibility: str
    status: str = "PLANNED"
    kind: ArchitectureNodeKind = ArchitectureNodeKind.SYSTEM
    children: list["Component"] = Field(default_factory=list, max_length=7)


class Relationship(BaseModel):
    source: str
    target: str
    relationship_type: str
    description: str = ""


class Architecture(BaseModel):
    version: int = 0
    summary: str = ""
    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> "Architecture":
        if len(self.components) > 8:
            raise ValueError("architecture allows at most 8 top-level components")

        seen: set[str] = set()
        total = 0

        def walk(nodes: list[Component], depth: int) -> None:
            nonlocal total
            for node in nodes:
                total += 1
                if total > 40:
                    raise ValueError("architecture allows at most 40 total nodes")
                if node.id in seen:
                    raise ValueError(f"duplicate architecture node id: {node.id}")
                seen.add(node.id)
                if depth >= 3 and node.children:
                    raise ValueError("architecture depth is capped at 3 levels")
                if depth == 2 and len(node.children) > 6:
                    raise ValueError("level-3 detail is capped at 6 children per node")
                walk(node.children, depth + 1)

        walk(self.components, 1)
        for relationship in self.relationships:
            if relationship.source not in seen or relationship.target not in seen:
                raise ValueError("architecture relationships must reference existing node ids")
        return self

    def all_components(self) -> list[Component]:
        nodes: list[Component] = []

        def collect(items: list[Component]) -> None:
            for item in items:
                nodes.append(item)
                collect(item.children)

        collect(self.components)
        return nodes

    def component_ids(self) -> set[str]:
        return {component.id for component in self.all_components()}

    def find_component(self, component_id: str) -> Component | None:
        return next((component for component in self.all_components() if component.id == component_id), None)

    def root_component_id_for(self, component_id: str) -> str | None:
        def contains(node: Component) -> bool:
            return node.id == component_id or any(contains(child) for child in node.children)

        root = next((component for component in self.components if contains(component)), None)
        return root.id if root else None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    owner: TaskOwner = TaskOwner.UNASSIGNED
    source: TaskSource = TaskSource.AGENT
    related_component: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    name: str
    goal: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    architecture_version: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("event"))
    project_id: str
    type: ProjectEventType
    source: ProjectEventSource = ProjectEventSource.HUMAN
    source_event_id: str | None = Field(default=None, max_length=512)
    timestamp: datetime = Field(default_factory=utcnow)
    occurred_at: datetime | None = None
    received_at: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_observation_metadata(self) -> "ProjectEvent":
        if self.source_event_id is not None:
            normalized = self.source_event_id.strip()
            self.source_event_id = normalized or None
        if self.occurred_at is None:
            self.occurred_at = self.timestamp
        return self


class ArchitectureChangeProposal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("proposal"))
    project_id: str
    base_architecture_version: int | None = Field(default=None, ge=0)
    reason: str
    evidence: list[str]
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=8)
    observed_change: str
    affected_components: list[str] = Field(default_factory=list)
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    impact: str
    recommended_option: ArchitectureOption
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)


class TaskProposal(BaseModel):
    title: str
    description: str = ""
    owner: TaskOwner = TaskOwner.HUMAN
    source: TaskSource = TaskSource.AGENT
    related_component: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class AgentAction(BaseModel):
    type: AgentActionType
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> "AgentAction":
        required = {
            AgentActionType.CREATE_TASK: {"task"},
            AgentActionType.UPDATE_TASK: {"task_id", "changes"},
            AgentActionType.ADD_PROJECT_NOTE: {"note"},
            AgentActionType.UPDATE_PROJECT_STATUS: {"status"},
            AgentActionType.PROPOSE_ARCHITECTURE_CHANGE: {"proposal"},
            AgentActionType.NO_ACTION: set(),
        }[self.type]
        missing = required.difference(self.payload)
        if missing:
            raise ValueError(f"missing payload fields for {self.type}: {sorted(missing)}")
        return self


class AgentDecision(BaseModel):
    summary: str
    actions: list[AgentAction] = Field(default_factory=list)
    architecture_review_required: bool = False
    evaluation: DriftEvaluation | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "AgentDecision":
        proposes = any(a.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE for a in self.actions)
        if proposes and not self.architecture_review_required:
            raise ValueError("architecture proposal requires architecture_review_required=true")
        if self.architecture_review_required and not proposes:
            raise ValueError("architecture_review_required=true requires a proposal action")
        if proposes and self.evaluation is None:
            raise ValueError("architecture proposal requires a DriftEvaluation")
        return self


class AgentRunResult(BaseModel):
    project_id: str
    event_id: str
    agent_run_id: str
    summary: str
    actions: list[AgentAction]
    architecture_review_required: bool
    proposal_ids: list[str] = Field(default_factory=list)
    evaluation: DriftEvaluation | None = None
    provider: str
    model: str
    result: Literal["SUCCESS", "ERROR"]
    error: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime = Field(default_factory=utcnow)
    replayed: bool = False


class ObservationClaimState(StrEnum):
    CLAIMED = "CLAIMED"
    REPLAY = "REPLAY"
    IN_PROGRESS = "IN_PROGRESS"


class ObservationClaim(BaseModel):
    state: ObservationClaimState
    event: ProjectEvent
    run_id: str
    existing_result: AgentRunResult | None = None


class ProjectActivity(BaseModel):
    events: list[ProjectEvent] = Field(default_factory=list)
    agent_runs: list[AgentRunResult] = Field(default_factory=list)


class ProjectContext(BaseModel):
    project: Project
    architecture: Architecture
    tasks: list[Task]
    pending_proposals: list[ArchitectureChangeProposal]
    recent_notes: list[str] = Field(default_factory=list)
