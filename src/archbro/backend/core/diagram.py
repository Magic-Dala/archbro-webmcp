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
    SCOPE = "SCOPE"
    PRIMARY = "PRIMARY"
    CONTEXT = "CONTEXT"


class DiagramEdgeProjectionKind(StrEnum):
    AUTHORED = "AUTHORED"
    DERIVED_CROSSING = "DERIVED_CROSSING"


class DiagramEdgeLayoutRole(StrEnum):
    BACKBONE = "BACKBONE"
    CROSS_CUTTING = "CROSS_CUTTING"


class DiagramRelationshipCategory(StrEnum):
    FLOW = "FLOW"
    DATA = "DATA"
    EVENT = "EVENT"
    ACCESS = "ACCESS"
    OBSERVABILITY = "OBSERVABILITY"
    VALIDATION = "VALIDATION"
    DELIVERY = "DELIVERY"
    SUPPORT = "SUPPORT"


_BACKBONE_RELATIONSHIP_CATEGORIES = frozenset(
    {
        DiagramRelationshipCategory.FLOW,
        DiagramRelationshipCategory.DATA,
        DiagramRelationshipCategory.EVENT,
    }
)


def _relationship_category(semantic_type: str) -> DiagramRelationshipCategory:
    normalized = "_".join(str(semantic_type or "").strip().upper().replace("-", "_").split())
    if any(stem in normalized for stem in ("AUTHENTICAT", "AUTHORIZ", "ACCESS", "PERMISSION", "IDENTITY")):
        return DiagramRelationshipCategory.ACCESS
    if any(stem in normalized for stem in ("MONITOR", "OBSERV", "TELEMETR", "METRIC", "TRACE", "LOG")):
        return DiagramRelationshipCategory.OBSERVABILITY
    if any(stem in normalized for stem in ("VALIDAT", "VERIF", "TEST", "CHECK", "LINT")):
        return DiagramRelationshipCategory.VALIDATION
    if any(stem in normalized for stem in ("PROVISION", "DEPLOY", "RELEASE", "OPERAT", "RUNS")):
        return DiagramRelationshipCategory.DELIVERY
    if any(stem in normalized for stem in ("SUBSCRIB", "PUBLISH", "CONSUM", "EMIT", "FANOUT", "EVENT", "STREAM", "NOTIFY", "BROADCAST")):
        return DiagramRelationshipCategory.EVENT
    if any(stem in normalized for stem in ("PERSIST", "READ", "WRITE", "STORE", "APPEND", "QUERY", "SCHEMA", "DATABASE")):
        return DiagramRelationshipCategory.DATA
    if any(stem in normalized for stem in ("CALL", "INVOK", "COMMAND", "REQUEST", "DISPATCH", "SEND", "RECEIVE", "CONNECT", "UPDATE", "TRIGGER", "ROUTE", "DELEGAT")):
        return DiagramRelationshipCategory.FLOW
    return DiagramRelationshipCategory.SUPPORT


def _layout_role_for_semantic_types(semantic_types: Iterable[str]) -> DiagramEdgeLayoutRole:
    categories = {_relationship_category(item) for item in semantic_types if str(item).strip()}
    if categories & _BACKBONE_RELATIONSHIP_CATEGORIES:
        return DiagramEdgeLayoutRole.BACKBONE
    return DiagramEdgeLayoutRole.CROSS_CUTTING


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
    layout_role: DiagramEdgeLayoutRole = DiagramEdgeLayoutRole.BACKBONE
    provenance: list[RelationshipProvenance] = Field(default_factory=list)


class DiagramView(BaseModel):
    diagram_version: Literal["archbro.diagram.v1"] = DIAGRAM_VERSION
    architecture_version: int
    summary: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


def map_edge_ids(diagram: DiagramView) -> frozenset[str]:
    edges = sorted(diagram.edges, key=lambda edge: edge.id)
    if len(edges) <= 1:
        return frozenset(edge.id for edge in edges)

    backbone = [edge for edge in edges if edge.layout_role == DiagramEdgeLayoutRole.BACKBONE]

    def reduced_backbone_ids(candidates: list[DiagramEdge]) -> set[str]:
        if len(candidates) <= 1:
            return {edge.id for edge in candidates}

        # MAP renders one visual connection per directed endpoint pair. Parallel
        # backbone semantics must therefore collapse before transitive reduction;
        # otherwise two A->B edges incorrectly prove each other redundant and
        # both disappear. Prefer richer aggregate provenance, then stable id.
        representative_by_pair: dict[tuple[str, str], DiagramEdge] = {}
        for edge in candidates:
            pair = (edge.source, edge.target)
            current = representative_by_pair.get(pair)
            if current is None or (-len(edge.provenance), edge.id) < (-len(current.provenance), current.id):
                representative_by_pair[pair] = edge
        candidates = sorted(representative_by_pair.values(), key=lambda edge: edge.id)
        if len(candidates) <= 1:
            return {edge.id for edge in candidates}

        adjacency: dict[str, list[DiagramEdge]] = defaultdict(list)
        indegree = {node.id: 0 for node in diagram.nodes}
        for edge in candidates:
            adjacency[edge.source].append(edge)
            indegree[edge.target] = indegree.get(edge.target, 0) + 1
        for group in adjacency.values():
            group.sort(key=lambda edge: edge.id)

        ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        pending = dict(indegree)
        visited = 0
        while ready:
            node_id = ready.pop(0)
            visited += 1
            for edge in adjacency.get(node_id, []):
                pending[edge.target] -= 1
                if pending[edge.target] == 0:
                    ready.append(edge.target)
                    ready.sort()

        if visited == len(indegree):
            selected: set[str] = set()
            for edge in candidates:
                stack = [edge.source]
                seen = {edge.source}
                alternate = False
                while stack and not alternate:
                    current = stack.pop()
                    for candidate in adjacency.get(current, []):
                        if candidate.id == edge.id:
                            continue
                        if candidate.target == edge.target:
                            alternate = True
                            break
                        if candidate.target not in seen:
                            seen.add(candidate.target)
                            stack.append(candidate.target)
                if not alternate:
                    selected.add(edge.id)
            return selected

        # MAP may omit a cycle edge, but READ/FULL retain the complete cycle.
        parent = {node.id: node.id for node in diagram.nodes}

        def find(node_id: str) -> str:
            while parent[node_id] != node_id:
                parent[node_id] = parent[parent[node_id]]
                node_id = parent[node_id]
            return node_id

        selected: set[str] = set()
        for edge in candidates:
            source_root, target_root = find(edge.source), find(edge.target)
            if source_root == target_root:
                continue
            parent[max(source_root, target_root)] = min(source_root, target_root)
            selected.add(edge.id)
        return selected

    selected = reduced_backbone_ids(backbone)

    # BACKBONE controls architecture flow. CROSS_CUTTING edges are used only
    # to connect an otherwise isolated architecture island in MAP; they never
    # become rank constraints. Prefer validation/delivery evidence over purely
    # observational/support edges, then prefer an edge aggregating more
    # canonical provenance.
    parent = {node.id: node.id for node in diagram.nodes}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(source: str, target: str) -> None:
        source_root, target_root = find(source), find(target)
        if source_root == target_root:
            return
        parent[max(source_root, target_root)] = min(source_root, target_root)

    edge_by_id = {edge.id: edge for edge in edges}
    for edge_id in selected:
        edge = edge_by_id[edge_id]
        union(edge.source, edge.target)

    category_priority = {
        DiagramRelationshipCategory.VALIDATION: 0,
        DiagramRelationshipCategory.DELIVERY: 1,
        DiagramRelationshipCategory.ACCESS: 2,
        DiagramRelationshipCategory.OBSERVABILITY: 3,
        DiagramRelationshipCategory.SUPPORT: 4,
    }

    def cross_key(edge: DiagramEdge) -> tuple[int, int, str, str, str]:
        semantic_types = [item.semantic_type for item in edge.provenance] or [edge.semantic_type]
        categories = {_relationship_category(item) for item in semantic_types}
        priority = min((category_priority.get(category, 5) for category in categories), default=5)
        return (priority, -len(edge.provenance), edge.source, edge.target, edge.id)

    cross_cutting = sorted(
        (edge for edge in edges if edge.layout_role == DiagramEdgeLayoutRole.CROSS_CUTTING),
        key=cross_key,
    )
    for edge in cross_cutting:
        if find(edge.source) == find(edge.target):
            continue
        selected.add(edge.id)
        union(edge.source, edge.target)

    return frozenset(selected)


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
        layout_role=_layout_role_for_semantic_types([relationship.relationship_type]),
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
    aggregate_by_pair: bool = False,
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
        if not collapsed and not aggregate_by_pair:
            authored.append(_authored_edge(record))
            continue
        group_semantic = "" if aggregate_by_pair else relationship.relationship_type
        grouped[(source_node_id, target_node_id, group_semantic)].append(record)

    derived: list[DiagramEdge] = []
    for (source_node_id, target_node_id, grouped_semantic_type), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: item.relationship_id)
        relationship_ids = [item.relationship_id for item in ordered]
        semantic_types = sorted({item.relationship.relationship_type for item in ordered})
        semantic_type = semantic_types[0] if len(semantic_types) == 1 else "MULTIPLE"
        label = semantic_type if len(semantic_types) == 1 else f"{len(ordered)} relationships"
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
                label=label,
                supporting_text="",
                projection_kind=DiagramEdgeProjectionKind.DERIVED_CROSSING,
                layout_role=_layout_role_for_semantic_types(semantic_types),
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
            aggregate_by_pair=any(component.children for component in architecture.components),
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
        return None

    def include_record(record: _AuthoredRelationship) -> bool:
        relationship = record.relationship
        return is_descendant(relationship.source) and is_descendant(relationship.target)

    direct_relationships = [
        record.provenance
        for record in records
        if (
            record.relationship.source == scope_component_id
            or record.relationship.target == scope_component_id
            or is_descendant(record.relationship.source) != is_descendant(record.relationship.target)
        )
    ]

    edges, _ = _project_edges(
        records=records,
        scope_key=scope_component_id,
        representative_for=representative,
        include_record=include_record,
        aggregate_by_pair=True,
    )

    nodes = [
        _diagram_node(
            scope,
            role=DiagramProjectionRole.SCOPE,
            index=index,
            tasks_by_component=tasks_by_component,
            proposals_by_component=proposals_by_component,
        )
    ] + [
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
