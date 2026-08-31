from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    Component,
    ProposalStatus,
    Relationship,
    Task,
    TaskStatus,
)


DIAGRAM_VERSION = "archbro.diagram.v1"


class DiagramHealth(StrEnum):
    PLANNED = "PLANNED"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CHANGE_PENDING = "CHANGE_PENDING"
    UNKNOWN = "UNKNOWN"


class DiagramStatus(BaseModel):
    canonical_status: str
    task_status: TaskStatus | None = None
    proposal_status: ProposalStatus | None = None
    health: DiagramHealth


class DiagramNode(BaseModel):
    id: str
    component_id: str
    semantic_kind: ArchitectureNodeKind
    semantic_type: str
    label: str
    responsibility: str
    supporting_text: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    depth: int
    status: DiagramStatus


class DiagramEdge(BaseModel):
    id: str
    source: str
    target: str
    semantic_type: str
    label: str
    supporting_text: str = ""


class DiagramView(BaseModel):
    diagram_version: Literal["archbro.diagram.v1"] = DIAGRAM_VERSION
    architecture_version: int
    summary: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


def _node_id(component_id: str) -> str:
    return f"node:{component_id}"


def _edge_id(relationship: Relationship, occurrence: int) -> str:
    payload = json.dumps(
        [
            relationship.source,
            relationship.target,
            relationship.relationship_type,
            relationship.description,
            occurrence,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"edge:{digest}"


def _aggregate_task_status(tasks: Iterable[Task]) -> TaskStatus | None:
    statuses = {task.status for task in tasks}
    for status in (
        TaskStatus.BLOCKED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.TODO,
        TaskStatus.DONE,
    ):
        if status in statuses:
            return status
    return None


def _project_health(
    canonical_status: str,
    task_status: TaskStatus | None,
    proposal_status: ProposalStatus | None,
) -> DiagramHealth:
    if task_status == TaskStatus.BLOCKED:
        return DiagramHealth.BLOCKED
    if proposal_status == ProposalStatus.PENDING:
        return DiagramHealth.CHANGE_PENDING
    if task_status is not None:
        return DiagramHealth(task_status.value)

    normalized = canonical_status.strip().upper()
    if normalized in {
        DiagramHealth.PLANNED.value,
        DiagramHealth.TODO.value,
        DiagramHealth.IN_PROGRESS.value,
        DiagramHealth.BLOCKED.value,
        DiagramHealth.DONE.value,
    }:
        return DiagramHealth(normalized)
    return DiagramHealth.UNKNOWN


def project_diagram(
    architecture: Architecture,
    *,
    tasks: Iterable[Task] = (),
    proposals: Iterable[ArchitectureChangeProposal] = (),
) -> DiagramView:
    """Project canonical architecture state into deterministic renderer-facing Diagram IR."""

    component_ids = architecture.component_ids()
    for relationship in architecture.relationships:
        if relationship.source not in component_ids or relationship.target not in component_ids:
            raise ValueError(
                "diagram projection rejects dangling relationship: "
                f"{relationship.source}->{relationship.target}"
            )

    tasks_by_component: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        if task.related_component in component_ids:
            tasks_by_component[task.related_component].append(task)

    proposals_by_component: dict[str, list[ArchitectureChangeProposal]] = defaultdict(list)
    for proposal in proposals:
        if proposal.status != ProposalStatus.PENDING:
            continue
        for component_id in proposal.affected_components:
            if component_id in component_ids:
                proposals_by_component[component_id].append(proposal)

    nodes: list[DiagramNode] = []

    def project_components(
        components: list[Component], parent_id: str | None, depth: int
    ) -> None:
        for component in sorted(components, key=lambda item: item.id):
            node_id = _node_id(component.id)
            component_tasks = sorted(
                tasks_by_component.get(component.id, []),
                key=lambda task: (task.status.value, task.title, task.id),
            )
            component_proposals = sorted(
                proposals_by_component.get(component.id, []),
                key=lambda proposal: (proposal.reason, proposal.id),
            )
            task_status = _aggregate_task_status(component_tasks)
            proposal_status = ProposalStatus.PENDING if component_proposals else None
            supporting_text = [
                f"Task {task.status.value}: {task.title}" for task in component_tasks
            ] + [
                f"Pending change: {proposal.reason}"
                for proposal in component_proposals
            ]
            nodes.append(
                DiagramNode(
                    id=node_id,
                    component_id=component.id,
                    semantic_kind=component.kind,
                    semantic_type=component.type,
                    label=component.name,
                    responsibility=component.responsibility,
                    supporting_text=supporting_text,
                    parent_id=parent_id,
                    depth=depth,
                    status=DiagramStatus(
                        canonical_status=component.status,
                        task_status=task_status,
                        proposal_status=proposal_status,
                        health=_project_health(
                            component.status, task_status, proposal_status
                        ),
                    ),
                )
            )
            project_components(component.children, node_id, depth + 1)

    project_components(architecture.components, None, 1)

    edges: list[DiagramEdge] = []
    occurrences: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for relationship in sorted(
        architecture.relationships,
        key=lambda item: (
            item.source,
            item.target,
            item.relationship_type,
            item.description,
        ),
    ):
        key = (
            relationship.source,
            relationship.target,
            relationship.relationship_type,
            relationship.description,
        )
        occurrence = occurrences[key]
        occurrences[key] += 1
        edges.append(
            DiagramEdge(
                id=_edge_id(relationship, occurrence),
                source=_node_id(relationship.source),
                target=_node_id(relationship.target),
                semantic_type=relationship.relationship_type,
                label=relationship.relationship_type,
                supporting_text=relationship.description,
            )
        )

    return DiagramView(
        architecture_version=architecture.version,
        summary=architecture.summary,
        nodes=nodes,
        edges=edges,
    )
