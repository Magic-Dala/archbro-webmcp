from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import heapq
from typing import Any

from .contracts import Architecture


LAYOUT_VERSION = "archbro.layout.v1"


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    node_width: int = 224
    node_height: int = 148
    horizontal_gap: int = 96
    vertical_gap: int = 44
    padding_x: int = 48
    padding_y: int = 48
    route_margin: int = 36
    hierarchy_gap: int = 24
    parallel_edge_gap: int = 6


@dataclass(frozen=True, slots=True)
class LayoutPoint:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class PositionedNode:
    node_id: str
    x: float
    y: float
    width: float
    height: float
    layer: int
    order: int
    parent_id: str | None = None
    hierarchy_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutedEdge:
    edge_id: str
    source: str
    target: str
    points: tuple[LayoutPoint, ...]
    routing: str
    order: int


@dataclass(frozen=True, slots=True)
class PositionedGraph:
    layout_version: str
    diagram_version: str | int | None
    architecture_version: int | None
    width: float
    height: float
    nodes: tuple[PositionedNode, ...]
    edges: tuple[RoutedEdge, ...]
    stable_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _NodeSpec:
    node_id: str
    parent_id: str | None
    projection_role: str = "PRIMARY"


@dataclass(frozen=True, slots=True)
class _EdgeSpec:
    edge_id: str
    source: str
    target: str
    canonical_key: tuple[str, ...]


def layout_architecture(
    architecture: Architecture,
    *,
    config: LayoutConfig = LayoutConfig(),
) -> PositionedGraph:
    """Project the canonical Architecture directly into deterministic layout input.

    This adapter intentionally carries no viewer state and does not create a second
    architecture model. It can be removed once callers always provide Diagram IR.
    """

    nodes = _collect_nodes(architecture.components)
    edges = _normalize_edges(architecture.relationships, set(nodes))
    return _layout(
        nodes,
        edges,
        diagram_version="architecture-adapter.v1",
        architecture_version=architecture.version,
        config=config,
    )


def layout_diagram(
    diagram: Any,
    *,
    config: LayoutConfig = LayoutConfig(),
) -> PositionedGraph:
    """Lay out an Agent-1-style Diagram IR without depending on a concrete IR class.

    The boundary is structural on purpose: nodes need stable ``id`` values, edges
    need ``source``/``target``, and hierarchy may be expressed through ``parent_id``
    (or nested ``children``). Pydantic models and mappings are both accepted.
    """

    raw_nodes = _value(diagram, "nodes", ()) or ()
    raw_edges = _value(diagram, "edges", ()) or ()
    nodes = _collect_nodes(raw_nodes)
    edges = _normalize_edges(raw_edges, set(nodes))
    diagram_version = _value(diagram, "diagram_version", None)
    if diagram_version is None:
        diagram_version = _value(diagram, "version", None)
    architecture_version = _value(diagram, "architecture_version", None)
    return _layout(
        nodes,
        edges,
        diagram_version=diagram_version,
        architecture_version=architecture_version,
        config=config,
    )


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _collect_nodes(raw_nodes: Iterable[Any]) -> dict[str, _NodeSpec]:
    nodes: dict[str, _NodeSpec] = {}

    def visit(raw: Any, inherited_parent: str | None) -> None:
        raw_id = _value(raw, "id", None)
        if raw_id is None or not str(raw_id).strip():
            raise ValueError("diagram node requires a non-empty stable id")
        node_id = str(raw_id)
        explicit_parent = _value(raw, "parent_id", None)
        if explicit_parent is None:
            explicit_parent = _value(raw, "parent", None)
        parent_id = str(explicit_parent) if explicit_parent not in (None, "") else inherited_parent
        projection_role = str(_value(raw, "projection_role", "PRIMARY") or "PRIMARY").upper()
        existing = nodes.get(node_id)
        spec = _NodeSpec(
            node_id=node_id,
            parent_id=parent_id,
            projection_role=projection_role,
        )
        if existing is not None and existing != spec:
            raise ValueError(f"conflicting diagram node id: {node_id}")
        if existing is not None:
            return
        nodes[node_id] = spec
        for child in _value(raw, "children", ()) or ():
            visit(child, node_id)

    for raw in raw_nodes:
        visit(raw, None)
    return nodes


def _normalize_edges(raw_edges: Iterable[Any], node_ids: set[str]) -> tuple[_EdgeSpec, ...]:
    pending: list[tuple[tuple[str, ...], str | None, str, str]] = []
    for raw in raw_edges:
        source = str(_value(raw, "source", ""))
        target = str(_value(raw, "target", ""))
        if source not in node_ids or target not in node_ids:
            continue
        semantic = str(
            _value(raw, "relationship_type", None)
            or _value(raw, "semantic_type", None)
            or _value(raw, "kind", None)
            or _value(raw, "type", None)
            or ""
        )
        label = str(_value(raw, "label", "") or "")
        description = str(_value(raw, "description", "") or "")
        provided = _value(raw, "id", None)
        provided_id = str(provided) if provided not in (None, "") else None
        canonical = (source, target, semantic, label, description, provided_id or "")
        pending.append((canonical, provided_id, source, target))

    pending.sort(key=lambda item: item[0])
    used: Counter[str] = Counter()
    edges: list[_EdgeSpec] = []
    for canonical, provided_id, source, target in pending:
        if provided_id:
            base_id = provided_id
        else:
            digest = hashlib.sha1("\x1f".join(canonical).encode("utf-8")).hexdigest()[:12]
            base_id = f"edge-{digest}"
        used[base_id] += 1
        edge_id = base_id if used[base_id] == 1 else f"{base_id}:{used[base_id]}"
        edges.append(_EdgeSpec(edge_id=edge_id, source=source, target=target, canonical_key=canonical))
    return tuple(sorted(edges, key=lambda edge: (edge.source, edge.target, edge.edge_id, edge.canonical_key)))


def _hierarchy_paths(nodes: Mapping[str, _NodeSpec]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for node_id in sorted(nodes):
        chain = [node_id]
        seen = {node_id}
        parent = nodes[node_id].parent_id
        while parent in nodes and parent not in seen:
            chain.append(parent)
            seen.add(parent)
            parent = nodes[parent].parent_id
        result[node_id] = tuple(reversed(chain))
    return result


def _strongly_connected_components(
    node_ids: Sequence[str],
    edges: Sequence[_EdgeSpec],
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        adjacency[edge.source].append(edge.target)
    for node_id in adjacency:
        adjacency[node_id] = sorted(set(adjacency[node_id]))

    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlink[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)

        for target in adjacency[node_id]:
            if target not in indices:
                visit(target)
                lowlink[node_id] = min(lowlink[node_id], lowlink[target])
            elif target in on_stack:
                lowlink[node_id] = min(lowlink[node_id], indices[target])

        if lowlink[node_id] != indices[node_id]:
            return
        members: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            members.append(member)
            if member == node_id:
                break
        components.append(tuple(sorted(members)))

    for node_id in sorted(node_ids):
        if node_id not in indices:
            visit(node_id)
    return tuple(sorted(components))


def _assign_layers(
    nodes: Mapping[str, _NodeSpec],
    edges: Sequence[_EdgeSpec],
    hierarchy_paths: Mapping[str, tuple[str, ...]],
) -> dict[str, int]:
    node_ids = tuple(sorted(nodes))
    components = _strongly_connected_components(node_ids, edges)
    component_of = {node_id: index for index, component in enumerate(components) for node_id in component}

    outgoing: dict[int, set[int]] = {index: set() for index in range(len(components))}
    indegree = {index: 0 for index in range(len(components))}
    for edge in edges:
        source_component = component_of[edge.source]
        target_component = component_of[edge.target]
        if source_component == target_component or target_component in outgoing[source_component]:
            continue
        outgoing[source_component].add(target_component)
        indegree[target_component] += 1

    spans = {index: max(1, len(component)) for index, component in enumerate(components)}
    base: dict[int, int] = {}
    for index, component in enumerate(components):
        minimum = 0
        for offset, node_id in enumerate(component):
            hierarchy_depth = max(0, len(hierarchy_paths[node_id]) - 1)
            minimum = max(minimum, hierarchy_depth - (offset if len(component) > 1 else 0))
        base[index] = minimum

    ready = [index for index, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        component = heapq.heappop(ready)
        order.append(component)
        for target in sorted(outgoing[component]):
            base[target] = max(base[target], base[component] + spans[component])
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)

    layers: dict[str, int] = {}
    for component_index in order:
        members = components[component_index]
        for offset, node_id in enumerate(members):
            layers[node_id] = base[component_index] + (offset if len(members) > 1 else 0)
    return layers


def _layer_height(node_ids: Sequence[str], paths: Mapping[str, tuple[str, ...]], config: LayoutConfig) -> float:
    if not node_ids:
        return 0
    root_changes = sum(1 for index in range(1, len(node_ids)) if paths[node_ids[index - 1]][0] != paths[node_ids[index]][0])
    return (
        len(node_ids) * config.node_height
        + max(0, len(node_ids) - 1) * config.vertical_gap
        + root_changes * config.hierarchy_gap
    )


def _layout(
    nodes: Mapping[str, _NodeSpec],
    edges: Sequence[_EdgeSpec],
    *,
    diagram_version: str | int | None,
    architecture_version: int | None,
    config: LayoutConfig,
) -> PositionedGraph:
    if not nodes:
        empty_size = float(config.padding_x * 2 + config.route_margin * 2)
        return PositionedGraph(
            layout_version=LAYOUT_VERSION,
            diagram_version=diagram_version,
            architecture_version=architecture_version,
            width=empty_size,
            height=float(config.padding_y * 2 + config.route_margin * 2),
            nodes=(),
            edges=(),
            stable_order=(),
        )

    paths = _hierarchy_paths(nodes)
    layers = _assign_layers(nodes, edges, paths)
    layer_count = max(layers.values()) + 1
    by_layer: dict[int, list[str]] = {layer: [] for layer in range(layer_count)}
    for node_id in sorted(nodes):
        by_layer[layers[node_id]].append(node_id)
    for layer in by_layer:
        by_layer[layer].sort(
            key=lambda node_id: (
                0 if nodes[node_id].projection_role == "PRIMARY" else 1,
                paths[node_id],
                node_id,
            )
        )

    parallel_counts = Counter((edge.source, edge.target) for edge in edges)
    max_parallel_index = max((count - 1 for count in parallel_counts.values()), default=0)
    route_margin = config.route_margin + max_parallel_index * config.parallel_edge_gap

    heights = {layer: _layer_height(node_ids, paths, config) for layer, node_ids in by_layer.items()}
    max_layer_height = max(heights.values(), default=0)
    width = (
        config.padding_x * 2
        + route_margin * 2
        + layer_count * config.node_width
        + max(0, layer_count - 1) * config.horizontal_gap
    )
    height = config.padding_y * 2 + route_margin * 2 + max_layer_height
    node_area_top = config.padding_y + route_margin

    positioned: dict[str, PositionedNode] = {}
    stable_order: list[str] = []
    absolute_order = 0
    for layer in range(layer_count):
        node_ids = by_layer[layer]
        y = node_area_top + (max_layer_height - heights[layer]) / 2
        previous_root: str | None = None
        for row, node_id in enumerate(node_ids):
            root = paths[node_id][0]
            if row and root != previous_root:
                y += config.hierarchy_gap
            x = config.padding_x + route_margin + layer * (config.node_width + config.horizontal_gap)
            positioned[node_id] = PositionedNode(
                node_id=node_id,
                x=float(x),
                y=float(y),
                width=float(config.node_width),
                height=float(config.node_height),
                layer=layer,
                order=absolute_order,
                parent_id=nodes[node_id].parent_id,
                hierarchy_path=paths[node_id],
            )
            stable_order.append(node_id)
            absolute_order += 1
            y += config.node_height + config.vertical_gap
            previous_root = root

    pair_seen: Counter[tuple[str, str]] = Counter()
    routed: list[RoutedEdge] = []
    for edge_order, edge in enumerate(edges):
        pair = (edge.source, edge.target)
        parallel_index = pair_seen[pair]
        pair_seen[pair] += 1
        points, routing = _route_edge(
            positioned[edge.source],
            positioned[edge.target],
            parallel_index=parallel_index,
            parallel_count=parallel_counts[pair],
            edge_order=edge_order,
            graph_height=float(height),
            config=config,
        )
        routed.append(
            RoutedEdge(
                edge_id=edge.edge_id,
                source=edge.source,
                target=edge.target,
                points=points,
                routing=routing,
                order=edge_order,
            )
        )

    return PositionedGraph(
        layout_version=LAYOUT_VERSION,
        diagram_version=diagram_version,
        architecture_version=architecture_version,
        width=float(width),
        height=float(height),
        nodes=tuple(positioned[node_id] for node_id in stable_order),
        edges=tuple(routed),
        stable_order=tuple(stable_order),
    )


def _route_edge(
    source: PositionedNode,
    target: PositionedNode,
    *,
    parallel_index: int,
    parallel_count: int,
    edge_order: int,
    graph_height: float,
    config: LayoutConfig,
) -> tuple[tuple[LayoutPoint, ...], str]:
    offset = min(config.horizontal_gap - 12, 20 + parallel_index * config.parallel_edge_gap)
    centered = (parallel_index - (parallel_count - 1) / 2) * config.parallel_edge_gap
    top_corridor = float(config.padding_y / 2 + parallel_index * config.parallel_edge_gap)
    bottom_corridor = float(graph_height - config.padding_y / 2 - parallel_index * config.parallel_edge_gap)

    if source.node_id == target.node_id:
        sx = source.x + source.width
        sy = source.y + source.height / 2
        channel_x = sx + offset
        loop_y = source.y - min(config.vertical_gap / 2, 18 + parallel_index * 4)
        return _dedupe_points(
            (
                LayoutPoint(sx, sy),
                LayoutPoint(channel_x, sy),
                LayoutPoint(channel_x, loop_y),
                LayoutPoint(source.x + source.width / 2, loop_y),
                LayoutPoint(source.x + source.width / 2, source.y),
            )
        ), "self"

    if target.layer > source.layer:
        sx = source.x + source.width
        sy = source.y + source.height / 2
        tx = target.x
        ty = target.y + target.height / 2
        if target.layer == source.layer + 1:
            mid_x = (sx + tx) / 2 + centered
            return _dedupe_points(
                (
                    LayoutPoint(sx, sy),
                    LayoutPoint(mid_x, sy),
                    LayoutPoint(mid_x, ty),
                    LayoutPoint(tx, ty),
                )
            ), "forward"
        corridor_y = top_corridor if edge_order % 2 == 0 else bottom_corridor
        return _dedupe_points(
            (
                LayoutPoint(sx, sy),
                LayoutPoint(sx + offset, sy),
                LayoutPoint(sx + offset, corridor_y),
                LayoutPoint(tx - offset, corridor_y),
                LayoutPoint(tx - offset, ty),
                LayoutPoint(tx, ty),
            )
        ), "outer-forward"

    if target.layer == source.layer:
        sx = source.x + source.width
        sy = source.y + source.height / 2
        tx = target.x + target.width
        ty = target.y + target.height / 2
        channel_x = max(sx, tx) + offset
        return _dedupe_points(
            (
                LayoutPoint(sx, sy),
                LayoutPoint(channel_x, sy),
                LayoutPoint(channel_x, ty),
                LayoutPoint(tx, ty),
            )
        ), "same-layer"

    sx = source.x
    sy = source.y + source.height / 2
    tx = target.x + target.width
    ty = target.y + target.height / 2
    corridor_y = top_corridor if edge_order % 2 == 0 else bottom_corridor
    return _dedupe_points(
        (
            LayoutPoint(sx, sy),
            LayoutPoint(sx - offset, sy),
            LayoutPoint(sx - offset, corridor_y),
            LayoutPoint(tx + offset, corridor_y),
            LayoutPoint(tx + offset, ty),
            LayoutPoint(tx, ty),
        )
    ), "backward"


def _dedupe_points(points: Sequence[LayoutPoint]) -> tuple[LayoutPoint, ...]:
    result: list[LayoutPoint] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    return tuple(result)
