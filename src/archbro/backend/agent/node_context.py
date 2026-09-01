from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from archbro.backend.core.contracts import Architecture, Component, Relationship
from archbro.backend.core.diagram import _edge_id


Direction = Literal["upstream", "downstream", "both"]


class ArchitectureNodeNotFoundError(LookupError):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.node_id = node_id


class StaleArchitectureVersionError(ValueError):
    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"expected architecture v{expected}, current is v{current}")
        self.expected = expected
        self.current = current


@dataclass(frozen=True, slots=True)
class _RelationshipRecord:
    relationship_id: str
    relationship: Relationship

    @property
    def source_id(self) -> str:
        return f"node:{self.relationship.source}"

    @property
    def target_id(self) -> str:
        return f"node:{self.relationship.target}"


@dataclass(frozen=True, slots=True)
class _HierarchyIndex:
    components: dict[str, Component]
    parents: dict[str, str | None]
    children: dict[str, tuple[str, ...]]
    roots: dict[str, str]


def require_architecture_version(
    architecture: Architecture,
    expected_architecture_version: int | None,
) -> None:
    if (
        expected_architecture_version is not None
        and architecture.version != expected_architecture_version
    ):
        raise StaleArchitectureVersionError(
            expected_architecture_version,
            architecture.version,
        )


def _component_id(node_id: str, index: _HierarchyIndex) -> str:
    if not node_id.startswith("node:") or not node_id[5:]:
        raise ArchitectureNodeNotFoundError(node_id)
    component_id = node_id[5:]
    if component_id not in index.components:
        raise ArchitectureNodeNotFoundError(node_id)
    return component_id


def _hierarchy_index(architecture: Architecture) -> _HierarchyIndex:
    components: dict[str, Component] = {}
    parents: dict[str, str | None] = {}
    children: dict[str, tuple[str, ...]] = {}
    roots: dict[str, str] = {}

    def visit(component: Component, parent_id: str | None, root_id: str) -> None:
        components[component.id] = component
        parents[component.id] = parent_id
        children[component.id] = tuple(sorted(child.id for child in component.children))
        roots[component.id] = root_id
        for child in sorted(component.children, key=lambda item: item.id):
            visit(child, component.id, root_id)

    for root in sorted(architecture.components, key=lambda item: item.id):
        visit(root, None, root.id)
    return _HierarchyIndex(components, parents, children, roots)


def _relationship_records(architecture: Architecture) -> tuple[_RelationshipRecord, ...]:
    ordered = sorted(
        architecture.relationships,
        key=lambda item: (
            item.source,
            item.target,
            item.relationship_type,
            item.description,
        ),
    )
    occurrences: dict[tuple[str, str, str, str], int] = defaultdict(int)
    records: list[_RelationshipRecord] = []
    for relationship in ordered:
        key = (
            relationship.source,
            relationship.target,
            relationship.relationship_type,
            relationship.description,
        )
        occurrence = occurrences[key]
        occurrences[key] += 1
        records.append(
            _RelationshipRecord(
                relationship_id=_edge_id(relationship, occurrence),
                relationship=relationship,
            )
        )
    return tuple(sorted(records, key=lambda item: item.relationship_id))


def _authored_component(component: Component) -> dict[str, object]:
    kind = component.kind.value if hasattr(component.kind, "value") else str(component.kind)
    return {
        "node_id": f"node:{component.id}",
        "component_id": component.id,
        "name": component.name,
        "type": component.type,
        "responsibility": component.responsibility,
        "status": component.status,
        "kind": kind,
    }


def _relationship_payload(
    record: _RelationshipRecord,
    architecture_version: int,
) -> dict[str, object]:
    relationship = record.relationship
    return {
        "id": record.relationship_id,
        "source": record.source_id,
        "target": record.target_id,
        "relationship_type": relationship.relationship_type,
        "description": relationship.description,
        "provenance": {
            "kind": "CANONICAL_RELATIONSHIP",
            "architecture_version": architecture_version,
            "relationship_id": record.relationship_id,
            "source_component_id": relationship.source,
            "target_component_id": relationship.target,
            "source_node_id": record.source_id,
            "target_node_id": record.target_id,
            "semantic_type": relationship.relationship_type,
            "supporting_text": relationship.description,
        },
    }


def _directional_reachability(
    origin: str,
    direction: Literal["upstream", "downstream"],
    records: tuple[_RelationshipRecord, ...],
    max_hops: int,
) -> tuple[dict[str, int], set[str], bool]:
    adjacency: dict[str, list[_RelationshipRecord]] = defaultdict(list)
    for record in records:
        key = record.relationship.target if direction == "upstream" else record.relationship.source
        adjacency[key].append(record)
    for items in adjacency.values():
        items.sort(
            key=lambda record: (
                record.relationship_id,
                record.source_id if direction == "upstream" else record.target_id,
            )
        )

    visited = {origin}
    reached: dict[str, int] = {}
    relationship_ids: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(origin, 0)])
    hit_hop_limit = False

    while queue:
        current, hop = queue.popleft()
        candidates = adjacency.get(current, [])
        if hop >= max_hops:
            for record in candidates:
                neighbor = (
                    record.relationship.source
                    if direction == "upstream"
                    else record.relationship.target
                )
                if neighbor != origin and neighbor not in visited:
                    hit_hop_limit = True
                    break
            continue

        for record in candidates:
            relationship_ids.add(record.relationship_id)
            neighbor = (
                record.relationship.source
                if direction == "upstream"
                else record.relationship.target
            )
            if neighbor == origin or neighbor in visited:
                continue
            visited.add(neighbor)
            reached[neighbor] = hop + 1
            queue.append((neighbor, hop + 1))

    return reached, relationship_ids, hit_hop_limit


def build_node_context(
    architecture: Architecture,
    project_id: str,
    node_id: str,
    *,
    direction: Direction = "both",
    max_hops: int = 1,
    max_results: int = 20,
    expected_architecture_version: int | None = None,
) -> dict[str, object]:
    require_architecture_version(architecture, expected_architecture_version)
    index = _hierarchy_index(architecture)
    origin = _component_id(node_id, index)
    records = _relationship_records(architecture)

    directions = (
        ("upstream", "downstream") if direction == "both" else (direction,)
    )
    reached_by_direction: dict[str, dict[str, int]] = {}
    relationship_ids: set[str] = set()
    hit_hop_limit = False
    for current_direction in directions:
        reached, reached_relationships, limited = _directional_reachability(
            origin,
            current_direction,
            records,
            max_hops,
        )
        reached_by_direction[current_direction] = reached
        relationship_ids.update(reached_relationships)
        hit_hop_limit = hit_hop_limit or limited

    candidates: dict[str, dict[str, object]] = {}
    for current_direction, reached in reached_by_direction.items():
        for component_id, hop in reached.items():
            entry = candidates.setdefault(
                component_id,
                {"hop": hop, "matched_directions": set()},
            )
            entry["hop"] = min(int(entry["hop"]), hop)
            matched = entry["matched_directions"]
            assert isinstance(matched, set)
            matched.add(current_direction)

    ordered_ids = sorted(
        candidates,
        key=lambda component_id: (int(candidates[component_id]["hop"]), f"node:{component_id}"),
    )
    hit_result_limit = len(ordered_ids) > max_results
    kept_ids = ordered_ids[:max_results]
    allowed_ids = {origin, *kept_ids}

    nodes: list[dict[str, object]] = []
    direction_order = {"upstream": 0, "downstream": 1}
    for component_id in kept_ids:
        candidate = candidates[component_id]
        node = _authored_component(index.components[component_id])
        node["hop"] = int(candidate["hop"])
        matched = candidate["matched_directions"]
        assert isinstance(matched, set)
        node["matched_directions"] = sorted(matched, key=direction_order.__getitem__)
        nodes.append(node)

    record_by_id = {record.relationship_id: record for record in records}
    relationships = [
        _relationship_payload(record_by_id[relationship_id], architecture.version)
        for relationship_id in sorted(relationship_ids)
        if record_by_id[relationship_id].relationship.source in allowed_ids
        and record_by_id[relationship_id].relationship.target in allowed_ids
    ]

    parent_id = index.parents[origin]
    root_id = index.roots[origin]
    max_returned_hop = max((int(node["hop"]) for node in nodes), default=0)
    truncated = hit_result_limit or hit_hop_limit
    limit_reason = (
        "MAX_RESULTS" if hit_result_limit else "MAX_HOPS" if hit_hop_limit else None
    )
    return {
        "schema": "archbro.node_context.v1",
        "project_id": project_id,
        "architecture_version": architecture.version,
        "origin": _authored_component(index.components[origin]),
        "scope": {
            "root_node_id": f"node:{root_id}",
            "parent_node_id": f"node:{parent_id}" if parent_id else None,
            "child_node_ids": [f"node:{child_id}" for child_id in index.children[origin]],
        },
        "query": {
            "direction": direction,
            "max_hops": max_hops,
            "max_results": max_results,
        },
        "nodes": nodes,
        "relationships": relationships,
        "counts": {
            "nodes": len(nodes),
            "relationships": len(relationships),
            "max_hop": max_returned_hop,
        },
        "truncated": truncated,
        "limit_reason": limit_reason,
    }


def find_architecture_path(
    architecture: Architecture,
    project_id: str,
    source_id: str,
    target_id: str,
    *,
    max_hops: int = 8,
    expected_architecture_version: int | None = None,
) -> dict[str, object]:
    require_architecture_version(architecture, expected_architecture_version)
    index = _hierarchy_index(architecture)
    source = _component_id(source_id, index)
    target = _component_id(target_id, index)
    records = _relationship_records(architecture)

    if source == target:
        path_component_ids = [source]
        path_relationships: list[_RelationshipRecord] = []
        status = "FOUND"
    else:
        adjacency: dict[str, list[_RelationshipRecord]] = defaultdict(list)
        for record in records:
            adjacency[record.relationship.source].append(record)
        for items in adjacency.values():
            items.sort(key=lambda record: (record.relationship_id, record.target_id))

        queue: deque[str] = deque([source])
        depth = {source: 0}
        predecessor: dict[str, tuple[str, _RelationshipRecord]] = {}
        found = False
        hit_hop_limit = False
        while queue and not found:
            current = queue.popleft()
            current_depth = depth[current]
            if current_depth >= max_hops:
                if any(record.relationship.target not in depth for record in adjacency.get(current, [])):
                    hit_hop_limit = True
                continue
            for record in adjacency.get(current, []):
                neighbor = record.relationship.target
                if neighbor in depth:
                    continue
                depth[neighbor] = current_depth + 1
                predecessor[neighbor] = (current, record)
                if neighbor == target:
                    found = True
                    break
                queue.append(neighbor)

        if found:
            path_component_ids = [target]
            path_relationships = []
            cursor = target
            while cursor != source:
                previous, record = predecessor[cursor]
                path_relationships.append(record)
                path_component_ids.append(previous)
                cursor = previous
            path_component_ids.reverse()
            path_relationships.reverse()
            status = "FOUND"
        else:
            path_component_ids = []
            path_relationships = []
            status = "LIMIT_REACHED" if hit_hop_limit else "UNREACHABLE"

    return {
        "schema": "archbro.architecture_path.v1",
        "project_id": project_id,
        "architecture_version": architecture.version,
        "source_id": source_id,
        "target_id": target_id,
        "max_hops": max_hops,
        "status": status,
        "hops": len(path_relationships) if status == "FOUND" else None,
        "nodes": [_authored_component(index.components[item]) for item in path_component_ids],
        "relationships": [
            _relationship_payload(record, architecture.version)
            for record in path_relationships
        ],
    }
