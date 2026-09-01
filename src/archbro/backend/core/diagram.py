from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
SCOPED_DIAGRAM_VERSION = "archbro.scoped_diagram.v1"


class DiagramHealth(StrEnum):
    PLANNED = "PLANNED"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CHANGE_PENDING = "CHANGE_PENDING"
    UNKNOWN = "UNKNOWN"


class DiagramProjectionRole(StrEnum):
    PRIMARY = "PRIMARY"
    CONTEXT = "CONTEXT"


class DiagramEdgeProjectionKind(StrEnum):
    AUTHORED = "AUTHORED"
    DERIVED_CROSSING = "DERIVED_CROSSING"


class DiagramStatus(BaseModel):
    canonical_status: str
    task_status: TaskStatus | None = None
    proposal_status: ProposalStatus | None = None
    health: DiagramHealth


class RelationshipProvenance(BaseModel):
    kind: Literal["CANONICAL_RELATIONSHIP"] = "CANONICAL_RELATIONSHIP"
    architecture_version: int
    relationship_id: str
    source_component_id: str
    target_component_id: str
    source_node_id: str
    target_node_id: str
    semantic_type: str
    supporting_text: str = ""


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
    projection_role: DiagramProjectionRole = DiagramProjectionRole.PRIMARY
    child_count: int = 0


class DiagramEdge(BaseModel):
    id: str
    source: str
    target: str
    semantic_type: str
    label: str
    supporting_text: str = ""
    projection_kind: DiagramEdgeProjectionKind = DiagramEdgeProjectionKind.AUTHORED
    provenance: list[RelationshipProvenance] = Field(default_factory=list)


class DiagramView(BaseModel):
    diagram_version: Literal["archbro.diagram.v1"] = DIAGRAM_VERSION
    architecture_version: int
    summary: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


class DiagramScopePathEntry(BaseModel):
    component_id: str
    node_id: str
    label: str


class DiagramScope(BaseModel):
    component_id: str | None = None
    node_id: str | None = None
    label: str
    is_leaf: bool
    ancestor_path: list[DiagramScopePathEntry] = Field(default_factory=list)
    direct_relationships: list[RelationshipProvenance] = Field(default_factory=list)


@dataclass(frozen=True)
class ScopedDiagramProjection:
    architecture_version: int
    scope: DiagramScope
    diagram: DiagramView
    schema: Literal["archbro.scoped_diagram.v1"] = SCOPED_DIAGRAM_VERSION

    def model_dump(self, *, mode: str = "python") -> dict:
        return {
            "schema": self.schema,
            "architecture_version": self.architecture_version,
            "scope": self.scope.model_dump(mode=mode),
            "diagram": self.diagram.model_dump(mode=mode),
        }


class ArchitectureNodeNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class _ArchitectureIndex:
    components: dict[str, Component]
    parents: dict[str, str | None]
    depths: dict[str, int]
    paths: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class _AuthoredRelationship:
    relationship_id: str
    relationship: Relationship
    provenance: RelationshipProvenance


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


def _validate_relationship_endpoints(architecture: Architecture) -> None:
    component_ids = architecture.component_ids()
    for relationship in architecture.relationships:
        if relationship.source not in component_ids or relationship.target not in component_ids:
            raise ValueError(
                "diagram projection rejects dangling relationship: "
                f"{relationship.source}->{relationship.target}"
            )


def _build_index(architecture: Architecture) -> _ArchitectureIndex:
    components: dict[str, Component] = {}
    parents: dict[str, str | None] = {}
    depths: dict[str, int] = {}
    paths: dict[str, tuple[str, ...]] = {}

    def visit(
        nodes: Iterable[Component],
        *,
        parent_id: str | None,
        depth: int,
        path: tuple[str, ...],
    ) -> None:
        for component in sorted(nodes, key=lambda item: item.id):
            components[component.id] = component
            parents[component.id] = parent_id
            depths[component.id] = depth
            component_path = (*path, component.id)
            paths[component.id] = component_path
            visit(
                component.children,
                parent_id=component.id,
                depth=depth + 1,
                path=component_path,
            )

    visit(architecture.components, parent_id=None, depth=1, path=())
    return _ArchitectureIndex(
        components=components,
        parents=parents,
        depths=depths,
        paths=paths,
    )


def _overlay_maps(
    architecture: Architecture,
    *,
    tasks: Iterable[Task],
    proposals: Iterable[ArchitectureChangeProposal],
    aggregate_descendants: bool = False,
) -> tuple[
    dict[str, list[Task]],
    dict[str, list[ArchitectureChangeProposal]],
]:
    index = _build_index(architecture)
    tasks_by_component: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        path = index.paths.get(task.related_component or "")
        if path:
            targets = path if aggregate_descendants else path[-1:]
            for component_id in targets:
                tasks_by_component[component_id].append(task)

    proposals_by_component: dict[str, list[ArchitectureChangeProposal]] = defaultdict(list)
    proposal_ids_by_component: dict[str, set[str]] = defaultdict(set)
    for proposal in proposals:
        if proposal.status != ProposalStatus.PENDING:
            continue
        for component_id in proposal.affected_components:
            path = index.paths.get(component_id)
            if not path:
                continue
            targets = path if aggregate_descendants else path[-1:]
            for representative_id in targets:
                if proposal.id in proposal_ids_by_component[representative_id]:
                    continue
                proposal_ids_by_component[representative_id].add(proposal.id)
                proposals_by_component[representative_id].append(proposal)
    return tasks_by_component, proposals_by_component


def _diagram_node(
    component: Component,
    *,
    role: DiagramProjectionRole,
    index: _ArchitectureIndex,
    tasks_by_component: dict[str, list[Task]],
    proposals_by_component: dict[str, list[ArchitectureChangeProposal]],
) -> DiagramNode:
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
        f"Pending change: {proposal.reason}" for proposal in component_proposals
    ]
    parent_id = index.parents[component.id]
    return DiagramNode(
        id=_node_id(component.id),
        component_id=component.id,
        semantic_kind=component.kind,
        semantic_type=component.type,
        label=component.name,
        responsibility=component.responsibility,
        supporting_text=supporting_text,
        parent_id=_node_id(parent_id) if parent_id else None,
        depth=index.depths[component.id],
        status=DiagramStatus(
            canonical_status=component.status,
            task_status=task_status,
            proposal_status=proposal_status,
            health=_project_health(component.status, task_status, proposal_status),
        ),
        projection_role=role,
        child_count=len(component.children),
    )


def _authored_relationships(architecture: Architecture) -> list[_AuthoredRelationship]:
    occurrences: dict[tuple[str, str, str, str], int] = defaultdict(int)
    records: list[_AuthoredRelationship] = []
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
        relationship_id = _edge_id(relationship, occurrence)
        records.append(
            _AuthoredRelationship(
                relationship_id=relationship_id,
                relationship=relationship,
                provenance=RelationshipProvenance(
                    architecture_version=architecture.version,
                    relationship_id=relationship_id,
                    source_component_id=relationship.source,
                    target_component_id=relationship.target,
                    source_node_id=_node_id(relationship.source),
                    target_node_id=_node_id(relationship.target),
                    semantic_type=relationship.relationship_type,
                    supporting_text=relationship.description,
                ),
            )
        )
    return sorted(records, key=lambda item: item.relationship_id)


def _authored_edge(record: _AuthoredRelationship) -> DiagramEdge:
    relationship = record.relationship
    return DiagramEdge(
        id=record.relationship_id,
        source=_node_id(relationship.source),
        target=_node_id(relationship.target),
        semantic_type=relationship.relationship_type,
        label=relationship.relationship_type,
        supporting_text=relationship.description,
        projection_kind=DiagramEdgeProjectionKind.AUTHORED,
        provenance=[record.provenance],
    )


def _derived_edge_id(
    *,
    scope_key: str,
    source_node_id: str,
    target_node_id: str,
    semantic_type: str,
    relationship_ids: list[str],
) -> str:
    payload = json.dumps(
        [
            scope_key,
            source_node_id,
            target_node_id,
            semantic_type,
            sorted(relationship_ids),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "agg:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _project_edges(
    *,
    records: list[_AuthoredRelationship],
    scope_key: str,
    representative_for: Callable[[str], tuple[str, bool] | None],
    include_record: Callable[[_AuthoredRelationship], bool],
) -> tuple[list[DiagramEdge], set[str]]:
    authored: list[DiagramEdge] = []
    grouped: dict[tuple[str, str, str], list[_AuthoredRelationship]] = defaultdict(list)
    context_component_ids: set[str] = set()

    for record in records:
        if not include_record(record):
            continue
        relationship = record.relationship
        source_rep = representative_for(relationship.source)
        target_rep = representative_for(relationship.target)
        if source_rep is None or target_rep is None:
            continue
        source_component_id, source_is_context = source_rep
        target_component_id, target_is_context = target_rep
        if source_is_context:
            context_component_ids.add(source_component_id)
        if target_is_context:
            context_component_ids.add(target_component_id)
        if source_component_id == target_component_id:
            continue

        source_node_id = _node_id(source_component_id)
        target_node_id = _node_id(target_component_id)
        collapsed = (
            source_component_id != relationship.source
            or target_component_id != relationship.target
        )
        if not collapsed:
            authored.append(_authored_edge(record))
            continue
        grouped[(source_node_id, target_node_id, relationship.relationship_type)].append(record)

    derived: list[DiagramEdge] = []
    for (source_node_id, target_node_id, semantic_type), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: item.relationship_id)
        relationship_ids = [item.relationship_id for item in ordered]
        derived.append(
            DiagramEdge(
                id=_derived_edge_id(
                    scope_key=scope_key,
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    semantic_type=semantic_type,
                    relationship_ids=relationship_ids,
                ),
                source=source_node_id,
                target=target_node_id,
                semantic_type=semantic_type,
                label=semantic_type,
                supporting_text="",
                projection_kind=DiagramEdgeProjectionKind.DERIVED_CROSSING,
                provenance=[item.provenance for item in ordered],
            )
        )

    edges = sorted(
        [*authored, *derived],
        key=lambda edge: (edge.source, edge.target, edge.semantic_type, edge.id),
    )
    return edges, context_component_ids


def project_diagram(
    architecture: Architecture,
    *,
    tasks: Iterable[Task] = (),
    proposals: Iterable[ArchitectureChangeProposal] = (),
) -> DiagramView:
    """Project the complete canonical architecture into deterministic Diagram IR."""

    _validate_relationship_endpoints(architecture)
    index = _build_index(architecture)
    tasks_by_component, proposals_by_component = _overlay_maps(
        architecture,
        tasks=tasks,
        proposals=proposals,
    )
    nodes = [
        _diagram_node(
            index.components[component_id],
            role=DiagramProjectionRole.PRIMARY,
            index=index,
            tasks_by_component=tasks_by_component,
            proposals_by_component=proposals_by_component,
        )
        for component_id in sorted(index.components, key=lambda item: index.paths[item])
    ]
    edges = [_authored_edge(record) for record in _authored_relationships(architecture)]
    return DiagramView(
        architecture_version=architecture.version,
        summary=architecture.summary,
        nodes=nodes,
        edges=edges,
    )


def _external_representative(
    *,
    scope_path: tuple[str, ...],
    endpoint_path: tuple[str, ...],
) -> str:
    common = 0
    for left, right in zip(scope_path, endpoint_path):
        if left != right:
            break
        common += 1
    if common < len(endpoint_path):
        return endpoint_path[common]
    return endpoint_path[-1]


def project_scoped_diagram(
    architecture: Architecture,
    *,
    scope_component_id: str | None = None,
    tasks: Iterable[Task] = (),
    proposals: Iterable[ArchitectureChangeProposal] = (),
) -> ScopedDiagramProjection:
    """Project one root/component scope without creating a second topology truth."""

    _validate_relationship_endpoints(architecture)
    index = _build_index(architecture)
    tasks_by_component, proposals_by_component = _overlay_maps(
        architecture,
        tasks=tasks,
        proposals=proposals,
        aggregate_descendants=True,
    )
    records = _authored_relationships(architecture)

    if scope_component_id is None:
        primary_component_ids = [
            component.id for component in sorted(architecture.components, key=lambda item: item.id)
        ]

        def root_representative(component_id: str) -> tuple[str, bool]:
            return index.paths[component_id][0], False

        edges, _ = _project_edges(
            records=records,
            scope_key="root",
            representative_for=root_representative,
            include_record=lambda _record: True,
        )
        nodes = [
            _diagram_node(
                index.components[component_id],
                role=DiagramProjectionRole.PRIMARY,
                index=index,
                tasks_by_component=tasks_by_component,
                proposals_by_component=proposals_by_component,
            )
            for component_id in primary_component_ids
        ]
        return ScopedDiagramProjection(
            architecture_version=architecture.version,
            scope=DiagramScope(
                component_id=None,
                node_id=None,
                label="Overview",
                is_leaf=False,
                ancestor_path=[],
                direct_relationships=[],
            ),
            diagram=DiagramView(
                architecture_version=architecture.version,
                summary=architecture.summary,
                nodes=nodes,
                edges=edges,
            ),
        )

    scope = index.components.get(scope_component_id)
    if scope is None:
        raise ArchitectureNodeNotFoundError(scope_component_id)

    scope_path = index.paths[scope_component_id]
    ancestor_path = [
        DiagramScopePathEntry(
            component_id=component_id,
            node_id=_node_id(component_id),
            label=index.components[component_id].name,
        )
        for component_id in scope_path
    ]
    direct_relationships = [
        record.provenance
        for record in records
        if record.relationship.source == scope_component_id
        or record.relationship.target == scope_component_id
    ]
    if not scope.children:
        return ScopedDiagramProjection(
            architecture_version=architecture.version,
            scope=DiagramScope(
                component_id=scope.id,
                node_id=_node_id(scope.id),
                label=scope.name,
                is_leaf=True,
                ancestor_path=ancestor_path,
                direct_relationships=direct_relationships,
            ),
            diagram=DiagramView(
                architecture_version=architecture.version,
                summary=architecture.summary,
                nodes=[],
                edges=[],
            ),
        )

    primary_component_ids = sorted(child.id for child in scope.children)
    scope_path_length = len(scope_path)

    def is_descendant(component_id: str) -> bool:
        path = index.paths[component_id]
        return len(path) > scope_path_length and path[:scope_path_length] == scope_path

    def representative(component_id: str) -> tuple[str, bool] | None:
        if is_descendant(component_id):
            return index.paths[component_id][scope_path_length], False
        endpoint_path = index.paths[component_id]
        return (
            _external_representative(
                scope_path=scope_path,
                endpoint_path=endpoint_path,
            ),
            True,
        )

    def include_record(record: _AuthoredRelationship) -> bool:
        relationship = record.relationship
        if relationship.source == scope_component_id or relationship.target == scope_component_id:
            return False
        return is_descendant(relationship.source) or is_descendant(relationship.target)

    edges, context_component_ids = _project_edges(
        records=records,
        scope_key=scope_component_id,
        representative_for=representative,
        include_record=include_record,
    )
    context_component_ids.difference_update(primary_component_ids)
    context_component_ids.discard(scope_component_id)

    nodes = [
        _diagram_node(
            index.components[component_id],
            role=DiagramProjectionRole.PRIMARY,
            index=index,
            tasks_by_component=tasks_by_component,
            proposals_by_component=proposals_by_component,
        )
        for component_id in primary_component_ids
    ] + [
        _diagram_node(
            index.components[component_id],
            role=DiagramProjectionRole.CONTEXT,
            index=index,
            tasks_by_component=tasks_by_component,
            proposals_by_component=proposals_by_component,
        )
        for component_id in sorted(context_component_ids)
    ]

    return ScopedDiagramProjection(
        architecture_version=architecture.version,
        scope=DiagramScope(
            component_id=scope.id,
            node_id=_node_id(scope.id),
            label=scope.name,
            is_leaf=False,
            ancestor_path=ancestor_path,
            direct_relationships=direct_relationships,
        ),
        diagram=DiagramView(
            architecture_version=architecture.version,
            summary=architecture.summary,
            nodes=nodes,
            edges=edges,
        ),
    )
