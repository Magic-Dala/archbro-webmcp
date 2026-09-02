from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
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
    edge_port_gap: int = 18
    edge_channel_gap: int = 14
    edge_port_gutter: int = 16
    edge_port_max_spacing: int = 14
    endpoint_stub: int = 24
    micro_segment_floor: int = 8
    interior_segment_floor: int = 16


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
    layout_role: str = "BACKBONE"


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
    route_edge_ids: Collection[str] | None = None,
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
    graph = _layout(
        nodes,
        edges,
        diagram_version=diagram_version,
        architecture_version=architecture_version,
        config=config,
    )
    if route_edge_ids is None:
        return graph

    visible_edge_ids = frozenset(route_edge_ids)
    return PositionedGraph(
        layout_version=graph.layout_version,
        diagram_version=graph.diagram_version,
        architecture_version=graph.architecture_version,
        width=graph.width,
        height=graph.height,
        nodes=graph.nodes,
        edges=tuple(edge for edge in graph.edges if edge.edge_id in visible_edge_ids),
        stable_order=graph.stable_order,
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
    pending: list[tuple[tuple[str, ...], str | None, str, str, str]] = []
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
        layout_role = str(_value(raw, "layout_role", "BACKBONE") or "BACKBONE").upper()
        if layout_role not in {"BACKBONE", "CROSS_CUTTING"}:
            layout_role = "BACKBONE"
        provided = _value(raw, "id", None)
        provided_id = str(provided) if provided not in (None, "") else None
        canonical = (source, target, semantic, label, description, layout_role, provided_id or "")
        pending.append((canonical, provided_id, source, target, layout_role))

    pending.sort(key=lambda item: item[0])
    used: Counter[str] = Counter()
    edges: list[_EdgeSpec] = []
    for canonical, provided_id, source, target, layout_role in pending:
        if provided_id:
            base_id = provided_id
        else:
            digest = hashlib.sha1("\x1f".join(canonical).encode("utf-8")).hexdigest()[:12]
            base_id = f"edge-{digest}"
        used[base_id] += 1
        edge_id = base_id if used[base_id] == 1 else f"{base_id}:{used[base_id]}"
        edges.append(
            _EdgeSpec(
                edge_id=edge_id,
                source=source,
                target=target,
                canonical_key=canonical,
                layout_role=layout_role,
            )
        )
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
    *,
    respect_layout_role: bool = True,
) -> dict[str, int]:
    node_ids = tuple(sorted(nodes))
    rank_edges = tuple(
        edge
        for edge in edges
        if not respect_layout_role or edge.layout_role == "BACKBONE"
    )
    components = _strongly_connected_components(node_ids, rank_edges)
    component_of = {node_id: index for index, component in enumerate(components) for node_id in component}

    outgoing: dict[int, set[int]] = {index: set() for index in range(len(components))}
    indegree = {index: 0 for index in range(len(components))}
    for edge in rank_edges:
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


def _align_unconstrained_same_hop_targets(
    nodes: Mapping[str, _NodeSpec],
    edges: Sequence[_EdgeSpec],
    layers: Mapping[str, int],
) -> dict[str, int]:
    """Align free cross-cutting fan-out targets without making support edges rank constraints.

    BACKBONE remains the semantic rank source. A CROSS_CUTTING target may only
    follow a source's unique forward BACKBONE hop when that target is otherwise
    unconstrained by BACKBONE and lives in the same hierarchy group.
    """
    aligned = dict(layers)
    backbone_incident: set[str] = set()
    backbone_targets: dict[str, list[str]] = defaultdict(list)
    cross_targets: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.layout_role == "BACKBONE":
            backbone_incident.update((edge.source, edge.target))
            backbone_targets[edge.source].append(edge.target)
        else:
            cross_targets[edge.source].append(edge.target)

    for source in sorted(cross_targets):
        source_layer = aligned[source]
        forward_layers = sorted(
            {
                aligned[target]
                for target in backbone_targets.get(source, ())
                if aligned[target] > source_layer
            }
        )
        if len(forward_layers) != 1:
            continue
        target_layer = forward_layers[0]
        for target in sorted(set(cross_targets[source])):
            if target in backbone_incident:
                continue
            if nodes[target].parent_id != nodes[source].parent_id:
                continue
            if aligned[target] <= target_layer:
                aligned[target] = target_layer
    return aligned


def _order_layers_by_topology(
    nodes: Mapping[str, _NodeSpec],
    edges: Sequence[_EdgeSpec],
    layers: Mapping[str, int],
    paths: Mapping[str, tuple[str, ...]],
) -> dict[int, list[str]]:
    # Deterministically reduce avoidable crossings while preserving hierarchy groups.
    layer_count = max(layers.values()) + 1
    by_layer: dict[int, list[str]] = {layer: [] for layer in range(layer_count)}
    for node_id in sorted(nodes):
        by_layer[layers[node_id]].append(node_id)

    def fallback_key(node_id: str) -> tuple[Any, ...]:
        return (
            0 if nodes[node_id].projection_role == "PRIMARY" else 1,
            paths[node_id][0] if len(paths[node_id]) > 1 else "",
            paths[node_id],
            node_id,
        )

    for layer in by_layer:
        by_layer[layer].sort(key=fallback_key)

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source_layer = layers[edge.source]
        target_layer = layers[edge.target]
        if source_layer < target_layer:
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)

    def positions() -> dict[str, float]:
        return {
            node_id: float(index)
            for layer in range(layer_count)
            for index, node_id in enumerate(by_layer[layer])
        }

    def reorder(layer: int, neighbors: Mapping[str, Sequence[str]], pos: Mapping[str, float]) -> None:
        def key(node_id: str) -> tuple[Any, ...]:
            usable = [pos[neighbor] for neighbor in neighbors.get(node_id, ()) if neighbor in pos]
            barycenter = sum(usable) / len(usable) if usable else float("inf")
            return (
                0 if nodes[node_id].projection_role == "PRIMARY" else 1,
                paths[node_id][0] if len(paths[node_id]) > 1 else "",
                0 if usable else 1,
                barycenter,
                paths[node_id],
                node_id,
            )

        by_layer[layer].sort(key=key)

    # Two deterministic forward/backward sweeps are enough for the small scoped
    # graphs ArchBro renders and avoid turning layout into an iterative heuristic.
    for _ in range(2):
        pos = positions()
        for layer in range(1, layer_count):
            reorder(layer, incoming, pos)
            pos = positions()
        for layer in range(layer_count - 2, -1, -1):
            reorder(layer, outgoing, pos)
            pos = positions()

    return by_layer


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

    routed_edges = tuple(edges)
    paths = _hierarchy_paths(nodes)
    # Layout is canonical and reading-mode agnostic. MAP/READ/FULL may disclose
    # different relationship sets, but they must never move nodes or reroute a
    # relationship merely because the viewer changed information level. Every
    # authored relationship participates in topological rank so a simple
    # left-to-right chain stays a simple left-to-right chain instead of being
    # collapsed into one column and repaired later with avoidable detours.
    layers = _assign_layers(nodes, edges, paths, respect_layout_role=False)
    layer_count = max(layers.values()) + 1
    by_layer = _order_layers_by_topology(nodes, edges, layers, paths)

    layout_parallel_counts = Counter((edge.source, edge.target) for edge in edges)
    max_parallel_index = max((count - 1 for count in layout_parallel_counts.values()), default=0)
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

    routing_parallel_counts = Counter((edge.source, edge.target) for edge in routed_edges)
    pair_seen: Counter[tuple[str, str]] = Counter()
    ports_by_side: dict[tuple[str, str], list[tuple[_EdgeSpec, str]]] = defaultdict(list)
    channels_by_gap: dict[tuple[int, int], list[_EdgeSpec]] = defaultdict(list)
    for edge in routed_edges:
        source_layer = positioned[edge.source].layer
        target_layer = positioned[edge.target].layer
        if target_layer > source_layer:
            source_side, target_side = "RIGHT", "LEFT"
        elif target_layer == source_layer:
            source_side = target_side = "RIGHT"
        else:
            source_side, target_side = "LEFT", "RIGHT"
        ports_by_side[(edge.source, source_side)].append((edge, "source"))
        if edge.target != edge.source:
            ports_by_side[(edge.target, target_side)].append((edge, "target"))
        if abs(target_layer - source_layer) == 1:
            channels_by_gap[(source_layer, target_layer)].append(edge)

    def isolated_gap_positions(
        groups: Mapping[Any, Sequence[_EdgeSpec]],
    ) -> dict[str, tuple[int, int]]:
        """Assign one deterministic channel per relationship.

        Relationships never share fan-in/fan-out trunks. Each edge owns its
        source port, gap channel, and target port so crossings cannot imply a
        semantic junction that does not exist in the architecture.
        """
        positions: dict[str, tuple[int, int]] = {}
        for group in groups.values():
            ordered = sorted(
                group,
                key=lambda edge: (
                    # Channel order follows endpoint topology first. Role is a
                    # tie-breaker only; role-first ordering can invert channels
                    # and force overlapping/crossing horizontal approaches.
                    positioned[edge.source].y + positioned[edge.target].y,
                    0 if edge.layout_role == "BACKBONE" else 1,
                    edge.source,
                    edge.target,
                    edge.edge_id,
                ),
            )
            for index, edge in enumerate(ordered):
                positions[edge.edge_id] = (index, len(ordered))
        return positions

    port_positions: dict[tuple[str, str], tuple[int, int]] = {}
    for group in ports_by_side.values():
        ordered = sorted(
            group,
            key=lambda item: (
                # Preserve the vertical order of the opposite endpoint first.
                # Role is only a tie-breaker; grouping BACKBONE/CROSS_CUTTING
                # ahead of topology can invert fan-in/fan-out ports and force
                # otherwise avoidable edge crossings.
                positioned[item[0].target if item[1] == "source" else item[0].source].y,
                0 if item[0].layout_role == "BACKBONE" else 1,
                item[0].target,
                item[0].source,
                item[0].edge_id,
                item[1],
            ),
        )
        for index, (candidate, endpoint) in enumerate(ordered):
            port_positions[(candidate.edge_id, endpoint)] = (index, len(ordered))
    gap_channels = isolated_gap_positions(channels_by_gap)
    routed: list[RoutedEdge] = []
    positioned_nodes = tuple(positioned.values())
    for edge_order, edge in enumerate(routed_edges):
        pair = (edge.source, edge.target)
        parallel_index = pair_seen[pair]
        pair_seen[pair] += 1
        source_port_index, source_port_count = port_positions[(edge.edge_id, "source")]
        target_port_index, target_port_count = port_positions.get(
            (edge.edge_id, "target"),
            (source_port_index, source_port_count),
        )
        channel_index, channel_count = gap_channels.get(edge.edge_id, (0, 1))
        points, routing = _route_edge(
            positioned[edge.source],
            positioned[edge.target],
            positioned_nodes=positioned_nodes,
            parallel_index=parallel_index,
            parallel_count=routing_parallel_counts[pair],
            edge_order=edge_order,
            graph_height=float(height),
            source_port_index=source_port_index,
            source_port_count=source_port_count,
            target_port_index=target_port_index,
            target_port_count=target_port_count,
            channel_index=channel_index,
            channel_count=channel_count,
            layout_role=edge.layout_role,
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


def _local_corridor_y(
    source: PositionedNode,
    target: PositionedNode,
    *,
    positioned_nodes: Sequence[PositionedNode],
    parallel_index: int,
    edge_order: int,
    graph_height: float,
    config: LayoutConfig,
    lane_offset: float = 0.0,
) -> float:
    low_layer, high_layer = sorted((source.layer, target.layer))
    source_center = source.y + source.height / 2
    target_center = target.y + target.height / 2
    preferred = (
        (source_center + target_center) / 2
        + ((edge_order % 5) - 2) * config.parallel_edge_gap
        + lane_offset
    )
    clearance = float(
        max(8, config.parallel_edge_gap + parallel_index * config.parallel_edge_gap)
    )
    lower_bound = float(config.padding_y / 2 + parallel_index * config.parallel_edge_gap)
    upper_bound = float(
        graph_height - config.padding_y / 2 - parallel_index * config.parallel_edge_gap
    )
    obstacles = [
        node
        for node in positioned_nodes
        if low_layer < node.layer < high_layer
    ]
    if not obstacles:
        return min(max(preferred, lower_bound), upper_bound)

    intervals = sorted(
        (
            max(lower_bound, node.y - clearance),
            min(upper_bound, node.y + node.height + clearance),
        )
        for node in obstacles
    )
    merged: list[list[float]] = []
    for low, high in intervals:
        if not merged or low > merged[-1][1]:
            merged.append([low, high])
        else:
            merged[-1][1] = max(merged[-1][1], high)

    clamped_preferred = min(max(preferred, lower_bound), upper_bound)

    def is_safe(y: float) -> bool:
        return not any(low < y < high for low, high in merged)

    candidates = [lower_bound, upper_bound, clamped_preferred]
    for low, high in merged:
        candidates.extend((low, high))
    safe_candidates = [
        y for y in candidates if lower_bound <= y <= upper_bound and is_safe(y)
    ]
    if not safe_candidates:
        return lower_bound if abs(lower_bound - preferred) <= abs(upper_bound - preferred) else upper_bound
    return min(safe_candidates, key=lambda y: (abs(y - preferred), y))


def _automatic_port_spread(
    node: PositionedNode,
    index: float,
    count: int,
    *,
    config: LayoutConfig,
) -> float:
    """Spread same-side ports deterministically and symmetrically.

    Endpoint ordering is established by counterpart position before this helper
    is called.  This function only projects that stable order onto the node
    border, keeping a fixed gutter and bounded spacing so dense fan-in/fan-out
    remains readable without role-dependent drift.
    """
    center = node.y + node.height / 2
    if count <= 1:
        return center
    usable = max(0.0, node.height - 2.0 * config.edge_port_gutter)
    spacing = min(float(config.edge_port_max_spacing), usable / (count - 1))
    return center + (index - (count - 1) / 2) * spacing


def _collinear_forward(a: LayoutPoint, b: LayoutPoint, c: LayoutPoint) -> bool:
    first_x, first_y = b.x - a.x, b.y - a.y
    second_x, second_y = c.x - b.x, c.y - b.y
    cross = first_x * second_y - first_y * second_x
    dot = first_x * second_x + first_y * second_y
    return abs(cross) <= 0.0001 and dot > 0.0001


def _normalize_route_points(points: Sequence[LayoutPoint]) -> tuple[LayoutPoint, ...]:
    """Archify-style route normalization: dedupe and collapse forward collinear waypoints."""
    deduped: list[LayoutPoint] = []
    for point in points:
        if not deduped or abs(point.x - deduped[-1].x) > 0.0001 or abs(point.y - deduped[-1].y) > 0.0001:
            deduped.append(point)
    normalized: list[LayoutPoint] = []
    for point in deduped:
        while len(normalized) >= 2 and _collinear_forward(normalized[-2], normalized[-1], point):
            normalized.pop()
        normalized.append(point)
    return tuple(normalized)


def _horizontal_route_honors_sides(
    points: Sequence[LayoutPoint],
    *,
    direction: int = 1,
) -> bool:
    """Require horizontal departure/arrival to follow the endpoint side contract."""
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    normalized = _normalize_route_points(points)
    if len(normalized) < 2:
        return False
    first_start, first_end = normalized[0], normalized[1]
    last_start, last_end = normalized[-2], normalized[-1]
    return (
        abs(first_end.y - first_start.y) <= 0.0001
        and (first_end.x - first_start.x) * direction > 0.0001
        and abs(last_end.y - last_start.y) <= 0.0001
        and (last_end.x - last_start.x) * direction > 0.0001
    )


def _segment_intersects_node(
    start: LayoutPoint,
    end: LayoutPoint,
    node: PositionedNode,
    *,
    clearance: float = 2.0,
) -> bool:
    left = node.x - clearance
    right = node.x + node.width + clearance
    top = node.y - clearance
    bottom = node.y + node.height + clearance
    if abs(start.y - end.y) <= 0.0001:
        low, high = sorted((start.x, end.x))
        return top <= start.y <= bottom and max(low, left) < min(high, right)
    if abs(start.x - end.x) <= 0.0001:
        low, high = sorted((start.y, end.y))
        return left <= start.x <= right and max(low, top) < min(high, bottom)
    return True


def _route_clears_nodes(
    points: Sequence[LayoutPoint],
    source: PositionedNode,
    target: PositionedNode,
    positioned_nodes: Sequence[PositionedNode],
) -> bool:
    endpoint_ids = {source.node_id, target.node_id}
    for node in positioned_nodes:
        if node.node_id in endpoint_ids:
            continue
        for start, end in zip(points, points[1:]):
            if _segment_intersects_node(start, end, node):
                return False
    return True


def _port_has_corner_clearance(node: PositionedNode, point: LayoutPoint, *, config: LayoutConfig) -> bool:
    inset = min(float(config.edge_port_gutter), node.height / 2.0)
    return node.y + inset <= point.y <= node.y + node.height - inset


def _align_facing_ports(
    source: PositionedNode,
    target: PositionedNode,
    start: LayoutPoint,
    end: LayoutPoint,
    *,
    source_spread: bool,
    target_spread: bool,
    positioned_nodes: Sequence[PositionedNode],
    config: LayoutConfig,
    direction: int = 1,
) -> tuple[LayoutPoint, LayoutPoint]:
    """Port Archify alignFacingPorts for either horizontal facing direction."""
    if source_spread and target_spread:
        return start, end
    if abs(start.y - end.y) >= float(config.interior_segment_floor):
        return start, end

    align_end_to_start = (start, LayoutPoint(end.x, start.y))
    align_start_to_end = (LayoutPoint(start.x, end.y), end)
    candidates = (
        (align_end_to_start,)
        if source_spread
        else (align_start_to_end,)
        if target_spread
        else (align_end_to_start, align_start_to_end)
    )
    for candidate_start, candidate_end in candidates:
        points = (candidate_start, candidate_end)
        if (
            _port_has_corner_clearance(source, candidate_start, config=config)
            and _port_has_corner_clearance(target, candidate_end, config=config)
            and _horizontal_route_honors_sides(points, direction=direction)
            and _route_clears_nodes(points, source, target, positioned_nodes)
        ):
            return candidate_start, candidate_end
    return start, end


def _automatic_port_rhythm_bridge(
    source: PositionedNode,
    target: PositionedNode,
    start: LayoutPoint,
    end: LayoutPoint,
    *,
    positioned_nodes: Sequence[PositionedNode],
    config: LayoutConfig,
    direction: int = 1,
) -> tuple[LayoutPoint, ...] | None:
    """Port Archify automaticPortRhythmBridge for either facing direction."""
    if abs(start.y - end.y) >= float(config.interior_segment_floor):
        return None
    endpoint_stub = float(config.endpoint_stub)
    interior = float(config.interior_segment_floor)
    start_stub = LayoutPoint(start.x + direction * endpoint_stub, start.y)
    end_stub = LayoutPoint(end.x - direction * endpoint_stub, end.y)
    for channel_y in (max(start.y, end.y) + interior, min(start.y, end.y) - interior):
        candidate = _normalize_route_points(
            (
                start,
                start_stub,
                LayoutPoint(start_stub.x, channel_y),
                LayoutPoint(end_stub.x, channel_y),
                end_stub,
                end,
            )
        )
        if (
            _horizontal_route_honors_sides(candidate, direction=direction)
            and not _route_has_rhythm_failure(candidate, config=config)
            and _route_clears_nodes(candidate, source, target, positioned_nodes)
        ):
            return candidate
    return None


def _side_aware_horizontal_bridge_candidates(
    start: LayoutPoint,
    end: LayoutPoint,
    *,
    config: LayoutConfig,
    direction: int = 1,
) -> tuple[tuple[LayoutPoint, ...], ...]:
    """Port Archify sideAwareBridgeCandidates for either horizontal direction."""
    endpoint_stub = float(config.endpoint_stub)
    minimum_bridge = float(config.interior_segment_floor)
    start_stub = LayoutPoint(start.x + direction * endpoint_stub, start.y)
    end_stub = LayoutPoint(end.x - direction * endpoint_stub, end.y)
    raw: list[tuple[LayoutPoint, ...]] = []
    if abs(start.y - end.y) < minimum_bridge:
        for channel_y in (max(start.y, end.y) + minimum_bridge, min(start.y, end.y) - minimum_bridge):
            raw.append(
                (
                    start,
                    start_stub,
                    LayoutPoint(start_stub.x, channel_y),
                    LayoutPoint(end_stub.x, channel_y),
                    end_stub,
                    end,
                )
            )
    raw.extend(
        (
            (start, start_stub, LayoutPoint(end_stub.x, start_stub.y), end_stub, end),
            (start, start_stub, LayoutPoint(start_stub.x, end_stub.y), end_stub, end),
        )
    )
    result: list[tuple[LayoutPoint, ...]] = []
    for candidate in raw:
        normalized = _normalize_route_points(candidate)
        if (
            len(normalized) >= 2
            and _horizontal_route_honors_sides(normalized, direction=direction)
            and normalized not in result
        ):
            result.append(normalized)
    return tuple(result)


def _archify_adjacent_route(
    source: PositionedNode,
    target: PositionedNode,
    start: LayoutPoint,
    end: LayoutPoint,
    *,
    source_spread: bool,
    target_spread: bool,
    positioned_nodes: Sequence[PositionedNode],
    config: LayoutConfig,
    direction: int = 1,
) -> tuple[LayoutPoint, ...]:
    """Archify automatic routing order for adjacent horizontal facing ports."""
    start, end = _align_facing_ports(
        source,
        target,
        start,
        end,
        source_spread=source_spread,
        target_spread=target_spread,
        positioned_nodes=positioned_nodes,
        config=config,
        direction=direction,
    )
    delta_x = abs(start.x - end.x)
    delta_y = abs(start.y - end.y)
    direct = _normalize_route_points((start, end))
    if (
        (delta_x < 4.0 or delta_y < 4.0)
        and _horizontal_route_honors_sides(direct, direction=direction)
        and _route_clears_nodes(direct, source, target, positioned_nodes)
    ):
        return direct

    rhythm_bridge = _automatic_port_rhythm_bridge(
        source,
        target,
        start,
        end,
        positioned_nodes=positioned_nodes,
        config=config,
        direction=direction,
    )
    if rhythm_bridge is not None:
        return rhythm_bridge

    mid_x = (start.x + end.x) / 2.0
    horizontal_first = _normalize_route_points(
        (start, LayoutPoint(mid_x, start.y), LayoutPoint(mid_x, end.y), end)
    )
    mid_y = (start.y + end.y) / 2.0
    vertical_first = _normalize_route_points(
        (start, LayoutPoint(start.x, mid_y), LayoutPoint(end.x, mid_y), end)
    )
    midpoint_candidates = (horizontal_first, vertical_first)
    side_safe = tuple(
        candidate
        for candidate in midpoint_candidates
        if _horizontal_route_honors_sides(candidate, direction=direction)
    )
    side_aware = _side_aware_horizontal_bridge_candidates(
        start,
        end,
        config=config,
        direction=direction,
    )
    near_parallel_ports = delta_y < float(config.micro_segment_floor * 2)
    ordered = (
        (*side_aware, *side_safe, *tuple(candidate for candidate in midpoint_candidates if candidate not in side_safe))
        if near_parallel_ports
        else (*side_safe, *side_aware, *tuple(candidate for candidate in midpoint_candidates if candidate not in side_safe))
    )
    for candidate in ordered:
        if (
            not _route_has_rhythm_failure(candidate, config=config)
            and _route_clears_nodes(candidate, source, target, positioned_nodes)
        ):
            return candidate
    return side_safe[0] if side_safe else side_aware[0] if side_aware else horizontal_first


def _route_has_rhythm_failure(
    points: Sequence[LayoutPoint],
    *,
    config: LayoutConfig,
) -> bool:
    segments = list(zip(points, points[1:]))
    for index, (start, end) in enumerate(segments):
        length = abs(end.x - start.x) + abs(end.y - start.y)
        if length < config.micro_segment_floor:
            return True
        if 0 < index < len(segments) - 1 and length < config.interior_segment_floor:
            return True
    return False


def _route_edge(
    source: PositionedNode,
    target: PositionedNode,
    *,
    positioned_nodes: Sequence[PositionedNode],
    parallel_index: int,
    parallel_count: int,
    edge_order: int,
    graph_height: float,
    source_port_index: float,
    source_port_count: int,
    target_port_index: float,
    target_port_count: int,
    channel_index: int,
    channel_count: int,
    layout_role: str,
    config: LayoutConfig,
) -> tuple[tuple[LayoutPoint, ...], str]:
    # Keep endpoint stubs in the outer thirds of each inter-layer gap. The
    # middle third is reserved for adjacent relationship channels, preventing a
    # long-route vertical stub from becoming collinear with another edge's
    # forward channel.
    endpoint_band = max(18.0, config.horizontal_gap / 3.0 - 4.0)
    role_offset = config.parallel_edge_gap / 2 if layout_role == "CROSS_CUTTING" else 0.0

    def endpoint_offset(port_index: float) -> float:
        return min(
            endpoint_band,
            12.0
            + role_offset
            + port_index * config.parallel_edge_gap
            + parallel_index * max(2.0, config.parallel_edge_gap / 2),
        )

    source_offset = endpoint_offset(source_port_index)
    target_offset = endpoint_offset(target_port_index)
    centered = (parallel_index - (parallel_count - 1) / 2) * config.parallel_edge_gap

    def port_y(node: PositionedNode, index: float, count: int) -> float:
        return _automatic_port_spread(node, index, count, config=config)

    source_port_y = port_y(source, source_port_index, source_port_count)
    target_port_y = port_y(target, target_port_index, target_port_count)

    if source.node_id == target.node_id:
        sx = source.x + source.width
        sy = source_port_y
        channel_x = sx + source_offset
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
        sy = source_port_y
        tx = target.x
        ty = target_port_y
        if target.layer == source.layer + 1:
            routed = _archify_adjacent_route(
                source,
                target,
                LayoutPoint(sx, sy),
                LayoutPoint(tx, ty),
                source_spread=source_port_count > 1,
                target_spread=target_port_count > 1,
                positioned_nodes=positioned_nodes,
                config=config,
            )
            return routed, "straight" if len(routed) == 2 else "forward"
        lane_offset = (
            (config.edge_channel_gap * 2.0) * (1 if edge_order % 2 else -1)
            if layout_role == "CROSS_CUTTING"
            else 0.0
        )
        corridor_y = _local_corridor_y(
            source,
            target,
            positioned_nodes=positioned_nodes,
            parallel_index=parallel_index,
            edge_order=edge_order,
            graph_height=graph_height,
            config=config,
            lane_offset=lane_offset,
        )
        return _dedupe_points(
            (
                LayoutPoint(sx, sy),
                LayoutPoint(sx + source_offset, sy),
                LayoutPoint(sx + source_offset, corridor_y),
                LayoutPoint(tx - target_offset, corridor_y),
                LayoutPoint(tx - target_offset, ty),
                LayoutPoint(tx, ty),
            )
        ), "outer-forward"

    if target.layer == source.layer:
        sx = source.x + source.width
        sy = source_port_y
        tx = target.x + target.width
        ty = target_port_y
        # Same-layer bypasses own the outer side of the local layer gap; they
        # must not reuse vertical lanes reserved for forward/backward routes.
        same_layer_offset = min(
            config.horizontal_gap - 8,
            max(source_offset, target_offset) + config.edge_channel_gap * 2 + (edge_order % 3) * config.parallel_edge_gap,
        )
        channel_x = max(sx, tx) + same_layer_offset
        return _dedupe_points(
            (
                LayoutPoint(sx, sy),
                LayoutPoint(channel_x, sy),
                LayoutPoint(channel_x, ty),
                LayoutPoint(tx, ty),
            )
        ), "same-layer"

    sx = source.x
    sy = source_port_y
    tx = target.x + target.width
    ty = target_port_y
    if target.layer == source.layer - 1:
        routed = _archify_adjacent_route(
            source,
            target,
            LayoutPoint(sx, sy),
            LayoutPoint(tx, ty),
            source_spread=source_port_count > 1,
            target_spread=target_port_count > 1,
            positioned_nodes=positioned_nodes,
            config=config,
            direction=-1,
        )
        return routed, "straight" if len(routed) == 2 else "backward"
    # Backward relationships own an outer lane band so they cannot turn back
    # through the shorter ingress/egress stubs of forward relationships on the
    # same node side.
    backward_band = config.edge_channel_gap * 2
    backward_source_offset = min(config.horizontal_gap - 8, source_offset + backward_band)
    backward_target_offset = min(config.horizontal_gap - 8, target_offset + backward_band)
    lane_offset = (
        (config.edge_channel_gap * 2.0) * (1 if edge_order % 2 else -1)
        if layout_role == "CROSS_CUTTING"
        else 0.0
    )
    corridor_y = _local_corridor_y(
        source,
        target,
        positioned_nodes=positioned_nodes,
        parallel_index=parallel_index,
        edge_order=edge_order,
        graph_height=graph_height,
        config=config,
        lane_offset=lane_offset,
    )
    return _dedupe_points(
        (
            LayoutPoint(sx, sy),
            LayoutPoint(sx - backward_source_offset, sy),
            LayoutPoint(sx - backward_source_offset, corridor_y),
            LayoutPoint(tx + backward_target_offset, corridor_y),
            LayoutPoint(tx + backward_target_offset, ty),
            LayoutPoint(tx, ty),
        )
    ), "backward"


def _dedupe_points(points: Sequence[LayoutPoint]) -> tuple[LayoutPoint, ...]:
    result: list[LayoutPoint] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    return tuple(result)
