from __future__ import annotations

from itertools import combinations
from pathlib import Path

from archbro.backend.core.contracts import Architecture, Component, Relationship
from archbro.backend.core.diagram_layout import PositionedGraph, layout_architecture, layout_diagram


def component(node_id: str, *, children: list[Component] | None = None) -> Component:
    return Component(
        id=node_id,
        name=node_id.upper(),
        type="service",
        responsibility=f"Responsibility for {node_id}",
        children=children or [],
    )


def relationship(source: str, target: str, kind: str = "calls", description: str = "") -> Relationship:
    return Relationship(
        source=source,
        target=target,
        relationship_type=kind,
        description=description,
    )


def by_id(graph: PositionedGraph):
    return {node.node_id: node for node in graph.nodes}


def assert_no_node_overlap(graph: PositionedGraph) -> None:
    for left, right in combinations(graph.nodes, 2):
        separated = (
            left.x + left.width <= right.x
            or right.x + right.width <= left.x
            or left.y + left.height <= right.y
            or right.y + right.height <= left.y
        )
        assert separated, f"nodes overlap: {left.node_id}, {right.node_id}"


def _segment_crosses_interior(p1, p2, node) -> bool:
    left, right = node.x, node.x + node.width
    top, bottom = node.y, node.y + node.height
    if p1.x == p2.x:
        x = p1.x
        low, high = sorted((p1.y, p2.y))
        return left < x < right and max(low, top) < min(high, bottom)
    if p1.y == p2.y:
        y = p1.y
        low, high = sorted((p1.x, p2.x))
        return top < y < bottom and max(low, left) < min(high, right)
    raise AssertionError("layout routes must remain orthogonal")


def assert_edges_avoid_unrelated_nodes(graph: PositionedGraph) -> None:
    nodes = by_id(graph)
    for edge in graph.edges:
        for node in graph.nodes:
            if node.node_id in {edge.source, edge.target}:
                continue
            for start, end in zip(edge.points, edge.points[1:]):
                assert not _segment_crosses_interior(start, end, node), (
                    edge.edge_id,
                    node.node_id,
                    start,
                    end,
                )
        assert edge.source in nodes and edge.target in nodes


def assert_no_collinear_edge_overlap(edges) -> None:
    def segments(edge):
        return list(zip(edge.points, edge.points[1:]))

    def overlap_length(first, second) -> float:
        a1, a2 = first
        b1, b2 = second
        if a1.y == a2.y == b1.y == b2.y:
            return max(0.0, min(max(a1.x, a2.x), max(b1.x, b2.x)) - max(min(a1.x, a2.x), min(b1.x, b2.x)))
        if a1.x == a2.x == b1.x == b2.x:
            return max(0.0, min(max(a1.y, a2.y), max(b1.y, b2.y)) - max(min(a1.y, a2.y), min(b1.y, b2.y)))
        return 0.0

    for left, right in combinations(edges, 2):
        for left_segment in segments(left):
            for right_segment in segments(right):
                assert overlap_length(left_segment, right_segment) == 0, (left.edge_id, right.edge_id)


def _proper_orthogonal_crossing(first, second) -> bool:
    a1, a2 = first
    b1, b2 = second
    if a1.y == a2.y and b1.x == b2.x:
        return (
            min(a1.x, a2.x) < b1.x < max(a1.x, a2.x)
            and min(b1.y, b2.y) < a1.y < max(b1.y, b2.y)
        )
    if a1.x == a2.x and b1.y == b2.y:
        return (
            min(b1.x, b2.x) < a1.x < max(b1.x, b2.x)
            and min(a1.y, a2.y) < b1.y < max(a1.y, a2.y)
        )
    return False


def assert_no_unrelated_proper_crossings(edges) -> None:
    for left, right in combinations(edges, 2):
        if {left.source, left.target} & {right.source, right.target}:
            continue
        for left_segment in zip(left.points, left.points[1:]):
            for right_segment in zip(right.points, right.points[1:]):
                assert not _proper_orthogonal_crossing(left_segment, right_segment), (
                    left.edge_id,
                    right.edge_id,
                )


def assert_route_rhythm(edge) -> None:
    segments = list(zip(edge.points, edge.points[1:]))
    for index, (start, end) in enumerate(segments):
        length = abs(end.x - start.x) + abs(end.y - start.y)
        assert length >= 8, (edge.edge_id, index, length)
        if 0 < index < len(segments) - 1:
            assert length >= 16, (edge.edge_id, index, length)


def assert_endpoint_side_contract(graph: PositionedGraph) -> None:
    nodes = by_id(graph)
    for edge in graph.edges:
        source = nodes[edge.source]
        target = nodes[edge.target]
        if source.node_id == target.node_id:
            continue
        first, second = edge.points[:2]
        penultimate, last = edge.points[-2:]
        if target.layer > source.layer:
            assert second.y == first.y and second.x > first.x, edge.edge_id
            assert penultimate.y == last.y and penultimate.x < last.x, edge.edge_id
        elif target.layer < source.layer:
            assert second.y == first.y and second.x < first.x, edge.edge_id
            assert penultimate.y == last.y and penultimate.x > last.x, edge.edge_id
        else:
            assert second.y == first.y and second.x > first.x, edge.edge_id
            assert penultimate.y == last.y and penultimate.x > last.x, edge.edge_id


def test_layout_is_deterministic_under_incidental_input_ordering():
    components = [component("api"), component("db"), component("ui"), component("worker")]
    relationships = [
        relationship("ui", "api", "http"),
        relationship("api", "db", "reads"),
        relationship("api", "worker", "dispatches"),
    ]
    first = layout_architecture(Architecture(version=7, components=components, relationships=relationships))
    second = layout_architecture(
        Architecture(version=7, components=list(reversed(components)), relationships=list(reversed(relationships)))
    )

    assert first == second
    assert first.architecture_version == 7
    assert first.layout_version == "archbro.layout.v1"


def test_dag_uses_clear_forward_layers_and_safe_routes():
    graph = layout_architecture(
        Architecture(
            components=[component("a"), component("b"), component("c"), component("d")],
            relationships=[relationship("a", "b"), relationship("b", "c"), relationship("a", "d")],
        )
    )
    nodes = by_id(graph)

    assert nodes["a"].layer == 0
    assert nodes["b"].layer == nodes["d"].layer == 1
    assert nodes["c"].layer == 2
    assert nodes["a"].x < nodes["b"].x < nodes["c"].x
    assert_no_node_overlap(graph)
    assert_edges_avoid_unrelated_nodes(graph)


def test_dense_forward_graph_uses_local_corridors_instead_of_graph_enclosing_routes():
    graph = layout_architecture(
        Architecture(
            components=[component(node_id) for node_id in ("a", "b", "c", "d", "e")],
            relationships=[
                relationship("a", "b"),
                relationship("b", "c"),
                relationship("c", "d"),
                relationship("d", "e"),
                relationship("a", "c"),
                relationship("a", "d"),
                relationship("a", "e"),
                relationship("b", "d"),
                relationship("b", "e"),
            ],
        )
    )
    nodes = by_id(graph)
    long_edges = [edge for edge in graph.edges if edge.routing == "outer-forward"]

    assert len(long_edges) == 5
    for edge in long_edges:
        source = nodes[edge.source]
        target = nodes[edge.target]
        corridor_y = edge.points[2].y
        local_top = min(source.y, target.y) - 22
        local_bottom = max(source.y + source.height, target.y + target.height) + 22
        assert local_top <= corridor_y <= local_bottom, (edge.edge_id, corridor_y)
    assert_edges_avoid_unrelated_nodes(graph)


def test_all_relationships_preserve_left_to_right_topological_flow():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "architecture_version": 1,
            "nodes": [
                {"id": "delivery"},
                {"id": "web"},
                {"id": "realtime"},
                {"id": "api"},
                {"id": "db"},
            ],
            "edges": [
                {"id": "delivery-api", "source": "delivery", "target": "api", "layout_role": "CROSS_CUTTING"},
                {"id": "delivery-db", "source": "delivery", "target": "db", "layout_role": "CROSS_CUTTING"},
                {"id": "web-api", "source": "web", "target": "api", "layout_role": "BACKBONE"},
                {"id": "web-realtime", "source": "web", "target": "realtime", "layout_role": "BACKBONE"},
                {"id": "realtime-api", "source": "realtime", "target": "api", "layout_role": "CROSS_CUTTING"},
                {"id": "api-db", "source": "api", "target": "db", "layout_role": "BACKBONE"},
                {"id": "realtime-db", "source": "realtime", "target": "db", "layout_role": "BACKBONE"},
            ],
        }
    )
    nodes = by_id(graph)

    assert nodes["delivery"].layer == nodes["web"].layer == 0
    assert nodes["realtime"].layer == 1
    assert nodes["api"].layer == 2
    assert nodes["db"].layer == 3
    assert len(graph.edges) == 7
    assert_edges_avoid_unrelated_nodes(graph)


def test_simple_cross_cutting_chain_stays_straight_and_identical_in_map_read_full():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 101,
        "nodes": [{"id": node_id} for node_id in ("web", "api", "data")],
        "edges": [
            {"id": "web-api", "source": "web", "target": "api", "layout_role": "CROSS_CUTTING"},
            {"id": "api-data", "source": "api", "target": "data", "layout_role": "BACKBONE"},
        ],
    }
    full = layout_diagram(diagram)
    read = layout_diagram(diagram, route_edge_ids={"web-api", "api-data"})
    mapped = layout_diagram(diagram, route_edge_ids={"api-data"})

    nodes = by_id(full)
    assert [nodes[node_id].layer for node_id in ("web", "api", "data")] == [0, 1, 2]
    assert len({nodes[node_id].y for node_id in ("web", "api", "data")}) == 1
    assert all(edge.routing == "straight" and len(edge.points) == 2 for edge in full.edges)
    assert read.nodes == mapped.nodes == full.nodes
    assert read.edges == full.edges
    full_routes = {edge.edge_id: edge.points for edge in full.edges}
    assert {edge.edge_id: edge.points for edge in mapped.edges} == {"api-data": full_routes["api-data"]}


def test_adjacent_fan_in_and_fan_out_use_distinct_trunks():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "architecture_version": 23,
            "nodes": [{"id": node_id} for node_id in ("a", "b", "c", "d", "e")],
            "edges": [
                {"id": "a-c", "source": "a", "target": "c", "layout_role": "BACKBONE"},
                {"id": "b-c", "source": "b", "target": "c", "layout_role": "BACKBONE"},
                {"id": "c-d", "source": "c", "target": "d", "layout_role": "BACKBONE"},
                {"id": "c-e", "source": "c", "target": "e", "layout_role": "BACKBONE"},
            ],
        }
    )
    routed = {edge.edge_id: edge for edge in graph.edges}

    # Every relationship owns a distinct target approach and source departure.
    assert routed["a-c"].points[-2:] != routed["b-c"].points[-2:]
    assert routed["c-d"].points[:2] != routed["c-e"].points[:2]
    assert_no_collinear_edge_overlap(graph.edges)
    assert_edges_avoid_unrelated_nodes(graph)


def test_automatic_port_spread_is_symmetric_bounded_and_counterpart_ordered():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "nodes": [{"id": node_id} for node_id in ("hub", "a", "b", "c", "d")],
            "edges": [
                {"id": f"hub-{target}", "source": "hub", "target": target, "layout_role": "BACKBONE"}
                for target in ("a", "b", "c", "d")
            ],
        }
    )
    nodes = by_id(graph)
    routed = {edge.edge_id: edge for edge in graph.edges}
    targets = sorted(("a", "b", "c", "d"), key=lambda node_id: nodes[node_id].y)
    source_ys = [routed[f"hub-{target}"].points[0].y for target in targets]
    center = nodes["hub"].y + nodes["hub"].height / 2

    assert source_ys == sorted(source_ys)
    assert (source_ys[0] + source_ys[-1]) / 2 == center
    assert all(0 < right - left <= 14 for left, right in zip(source_ys, source_ys[1:]))
    assert_endpoint_side_contract(graph)


def test_close_adjacent_ports_follow_archify_align_facing_ports_before_bridge():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "nodes": [{"id": node_id} for node_id in "abcdef"],
            "edges": [
                {"id": edge_id, "source": source, "target": target, "layout_role": "BACKBONE"}
                for edge_id, source, target in (
                    ("ac", "a", "c"),
                    ("ae", "a", "e"),
                    ("bd", "b", "d"),
                    ("bf", "b", "f"),
                    ("ce", "c", "e"),
                    ("cf", "c", "f"),
                    ("ef", "e", "f"),
                )
            ],
        }
    )
    routed = {edge.edge_id: edge for edge in graph.edges}

    assert_route_rhythm(routed["ac"])
    assert len(routed["ac"].points) == 2
    assert routed["ac"].routing == "straight"
    assert routed["ac"].points[0].y == routed["ac"].points[-1].y
    assert_endpoint_side_contract(graph)
    assert_no_collinear_edge_overlap(graph.edges)
    assert_no_unrelated_proper_crossings(graph.edges)


def test_edges_do_not_form_source_or_target_buses():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "architecture_version": 24,
            "nodes": [{"id": node_id} for node_id in ("delivery", "web", "application", "realtime", "db")],
            "edges": [
                {"id": "delivery-application", "source": "delivery", "target": "application", "layout_role": "BACKBONE"},
                {"id": "web-application", "source": "web", "target": "application", "layout_role": "BACKBONE"},
                {"id": "web-realtime", "source": "web", "target": "realtime", "layout_role": "BACKBONE"},
                {"id": "application-db", "source": "application", "target": "db", "layout_role": "BACKBONE"},
                {"id": "realtime-db", "source": "realtime", "target": "db", "layout_role": "BACKBONE"},
            ],
        }
    )
    routed = {edge.edge_id: edge for edge in graph.edges}

    assert routed["web-application"].points[:2] != routed["web-realtime"].points[:2]
    assert routed["application-db"].points[-2:] != routed["realtime-db"].points[-2:]
    assert routed["delivery-application"].points[-2:] != routed["web-application"].points[-2:]
    assert_no_collinear_edge_overlap(graph.edges)
    assert_edges_avoid_unrelated_nodes(graph)


def test_disconnected_nodes_have_stable_order_and_do_not_overlap():
    graph = layout_architecture(Architecture(components=[component("c"), component("a"), component("b")]))

    assert graph.stable_order == ("a", "b", "c")
    assert {node.layer for node in graph.nodes} == {0}
    assert_no_node_overlap(graph)


def test_cycle_has_deterministic_fallback_and_backward_edge():
    architecture = Architecture(
        components=[component("c"), component("a"), component("b")],
        relationships=[relationship("b", "c"), relationship("c", "a"), relationship("a", "b")],
    )
    graph = layout_architecture(architecture)
    again = layout_architecture(architecture)
    nodes = by_id(graph)

    assert graph == again
    assert [nodes[node_id].layer for node_id in ("a", "b", "c")] == [0, 1, 2]
    backward = next(edge for edge in graph.edges if edge.source == "c" and edge.target == "a")
    assert backward.routing == "backward"
    assert len(backward.points) >= 4
    assert_no_node_overlap(graph)
    assert_edges_avoid_unrelated_nodes(graph)


def test_multiple_edges_get_stable_ids_and_distinct_routes():
    relationships = [
        relationship("a", "b", "calls", "one"),
        relationship("a", "b", "calls", "two"),
        relationship("a", "b", "streams", "three"),
    ]
    first = layout_architecture(
        Architecture(components=[component("a"), component("b")], relationships=relationships)
    )
    second = layout_architecture(
        Architecture(components=[component("b"), component("a")], relationships=list(reversed(relationships)))
    )

    assert first == second
    assert len({edge.edge_id for edge in first.edges}) == 3
    assert len({edge.points for edge in first.edges}) == 3
    assert_edges_avoid_unrelated_nodes(first)


def test_hierarchy_is_projected_without_mutating_architecture_model():
    architecture = Architecture(
        components=[
            component(
                "platform",
                children=[
                    component("api"),
                    component("worker"),
                ],
            ),
            component("client"),
        ]
    )
    graph = layout_architecture(architecture)
    nodes = by_id(graph)

    assert nodes["api"].parent_id == "platform"
    assert nodes["worker"].parent_id == "platform"
    assert nodes["api"].hierarchy_path == ("platform", "api")
    assert nodes["worker"].hierarchy_path == ("platform", "worker")
    assert nodes["api"].layer == nodes["worker"].layer == 1
    assert nodes["platform"].layer == nodes["client"].layer == 0
    assert nodes["api"].x > nodes["platform"].x
    assert abs(nodes["api"].y - nodes["worker"].y) == nodes["api"].height + 44
    assert_no_node_overlap(graph)


def test_diagram_ir_structural_adapter_preserves_versions_and_parent_grouping():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 12,
        "nodes": [
            {"id": "root"},
            {"id": "child-b", "parent_id": "root"},
            {"id": "child-a", "parent_id": "root"},
        ],
        "edges": [
            {"id": "root-to-a", "source": "root", "target": "child-a"},
            {"id": "root-to-b", "source": "root", "target": "child-b"},
        ],
    }
    graph = layout_diagram(diagram)
    nodes = by_id(graph)

    assert graph.diagram_version == "archbro.diagram.v1"
    assert graph.architecture_version == 12
    assert nodes["root"].layer == 0
    assert nodes["child-a"].layer == nodes["child-b"].layer == 1
    assert nodes["child-a"].order < nodes["child-b"].order
    assert_no_node_overlap(graph)
    assert_edges_avoid_unrelated_nodes(graph)


def test_scoped_layout_is_deterministic_and_primary_precedes_context_in_equal_rank():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 14,
        "nodes": [
            {"id": "node:a-context", "projection_role": "CONTEXT"},
            {"id": "node:z-primary", "projection_role": "PRIMARY"},
        ],
        "edges": [],
    }
    first = layout_diagram(diagram)
    second = layout_diagram(
        {
            **diagram,
            "nodes": list(reversed(diagram["nodes"])),
        }
    )

    assert first == second
    assert first.layout_version == "archbro.layout.v1"
    assert first.stable_order == ("node:z-primary", "node:a-context")
    assert {node.node_id for node in first.nodes} == {
        "node:z-primary",
        "node:a-context",
    }
    assert_no_node_overlap(first)


def test_topology_ordering_reduces_simple_adjacent_layer_crossing():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 22,
        "nodes": [{"id": node_id} for node_id in ("a", "b", "c", "d")],
        "edges": [
            {"id": "a-d", "source": "a", "target": "d", "layout_role": "BACKBONE"},
            {"id": "b-c", "source": "b", "target": "c", "layout_role": "BACKBONE"},
        ],
    }

    graph = layout_diagram(diagram)
    nodes = {node.node_id: node for node in graph.nodes}

    assert nodes["a"].layer == nodes["b"].layer == 0
    assert nodes["c"].layer == nodes["d"].layer == 1
    assert nodes["a"].y < nodes["b"].y
    assert nodes["d"].y < nodes["c"].y
    assert_no_unrelated_proper_crossings(graph.edges)


def test_route_subset_preserves_canonical_full_geometry():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 21,
        "nodes": [{"id": node_id} for node_id in ("delivery", "web", "realtime", "api", "db")],
        "edges": [
            {"id": "delivery-api", "source": "delivery", "target": "api", "layout_role": "CROSS_CUTTING"},
            {"id": "delivery-db", "source": "delivery", "target": "db", "layout_role": "CROSS_CUTTING"},
            {"id": "delivery-realtime", "source": "delivery", "target": "realtime", "layout_role": "CROSS_CUTTING"},
            {"id": "realtime-api", "source": "realtime", "target": "api", "layout_role": "CROSS_CUTTING"},
            {"id": "web-api", "source": "web", "target": "api", "layout_role": "BACKBONE"},
            {"id": "web-realtime", "source": "web", "target": "realtime", "layout_role": "BACKBONE"},
            {"id": "api-db", "source": "api", "target": "db", "layout_role": "BACKBONE"},
            {"id": "realtime-db", "source": "realtime", "target": "db", "layout_role": "BACKBONE"},
        ],
    }
    map_ids = {"web-api", "web-realtime", "api-db", "realtime-db"}
    full = layout_diagram(diagram)
    mode_routed = layout_diagram(diagram, route_edge_ids=map_ids)

    assert mode_routed.nodes == full.nodes
    assert mode_routed.width == full.width
    assert mode_routed.height == full.height
    assert {edge.edge_id for edge in mode_routed.edges} == map_ids
    full_routes = {edge.edge_id: edge.points for edge in full.edges}
    assert {edge.edge_id: edge.points for edge in mode_routed.edges} == {
        edge_id: full_routes[edge_id]
        for edge_id in map_ids
    }


def test_route_subset_keeps_full_semantic_node_placement():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 22,
        "nodes": [{"id": node_id} for node_id in ("delivery", "web", "realtime", "api", "db")],
        "edges": [
            {"id": "delivery-api", "source": "delivery", "target": "api", "layout_role": "CROSS_CUTTING"},
            {"id": "web-api", "source": "web", "target": "api", "layout_role": "BACKBONE"},
            {"id": "web-realtime", "source": "web", "target": "realtime", "layout_role": "BACKBONE"},
            {"id": "api-db", "source": "api", "target": "db", "layout_role": "BACKBONE"},
            {"id": "realtime-db", "source": "realtime", "target": "db", "layout_role": "BACKBONE"},
        ],
    }
    full = layout_diagram(diagram)
    mapped = layout_diagram(diagram, route_edge_ids={"delivery-api", "web-api", "web-realtime", "api-db", "realtime-db"})
    full_nodes = {node.node_id: (node.x, node.y, node.layer) for node in full.nodes}
    map_nodes = {node.node_id: (node.x, node.y, node.layer) for node in mapped.nodes}
    assert map_nodes == full_nodes
    assert map_nodes["delivery"][2] == map_nodes["web"][2] == 0
    assert map_nodes["realtime"][2] == map_nodes["api"][2] == 1
    assert map_nodes["db"][2] == 2


def test_map_visible_cross_cutting_fanout_targets_share_the_next_column():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 23,
        "nodes": [
            {"id": "http"},
            {"id": "identity"},
            {"id": "issues"},
            {"id": "projects"},
        ],
        "edges": [
            {
                "id": "http-identity",
                "source": "http",
                "target": "identity",
                "layout_role": "CROSS_CUTTING",
            },
            {
                "id": "http-issues",
                "source": "http",
                "target": "issues",
                "layout_role": "BACKBONE",
            },
            {
                "id": "http-projects",
                "source": "http",
                "target": "projects",
                "layout_role": "BACKBONE",
            },
        ],
    }

    full = layout_diagram(diagram)
    mapped = layout_diagram(
        diagram,
        route_edge_ids={"http-identity", "http-issues", "http-projects"},
    )
    full_nodes = by_id(full)
    map_nodes = by_id(mapped)

    # READ/FULL keep BACKBONE as rank truth, but the otherwise-free
    # cross-cutting direct target follows the source's unique next-hop column.
    assert full_nodes["identity"].layer == full_nodes["issues"].layer == full_nodes["projects"].layer
    assert full_nodes["identity"].x == full_nodes["issues"].x == full_nodes["projects"].x

    # MAP uses the same canonical node placement as READ/FULL; disclosure can
    # hide relationships, but it must never recompute the layout.
    assert map_nodes["http"].layer == 0
    assert map_nodes["identity"].layer == map_nodes["issues"].layer == map_nodes["projects"].layer == 1
    assert map_nodes["identity"].x == map_nodes["issues"].x == map_nodes["projects"].x
    assert map_nodes["identity"].x > map_nodes["http"].x


def test_dense_adjacent_read_full_uses_archify_port_spread_and_side_safe_routes():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 24,
        "nodes": [{"id": node_id} for node_id in ("api", "web", "data")],
        "edges": [
            {"id": "api-data", "source": "api", "target": "data", "layout_role": "BACKBONE"},
            {"id": "api-data-x", "source": "api", "target": "data", "layout_role": "CROSS_CUTTING"},
            {"id": "web-data", "source": "web", "target": "data", "layout_role": "CROSS_CUTTING"},
        ],
    }
    graph = layout_diagram(diagram)
    routed = {edge.edge_id: edge for edge in graph.edges}

    assert routed["api-data"].points[0].y != routed["api-data-x"].points[0].y
    assert len({edge.points[-1].y for edge in routed.values()}) == 3
    assert_endpoint_side_contract(graph)
    assert_edges_avoid_unrelated_nodes(graph)
    for edge in graph.edges:
        assert_route_rhythm(edge)


def test_read_full_separate_backbone_and_cross_cutting_source_trunks():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 25,
        "nodes": [{"id": node_id} for node_id in ("http", "issues", "projects", "identity", "audit")],
        "edges": [
            {"id": "http-issues", "source": "http", "target": "issues", "layout_role": "BACKBONE"},
            {"id": "http-projects", "source": "http", "target": "projects", "layout_role": "BACKBONE"},
            {"id": "http-identity", "source": "http", "target": "identity", "layout_role": "CROSS_CUTTING"},
            {"id": "http-audit", "source": "http", "target": "audit", "layout_role": "CROSS_CUTTING"},
        ],
    }
    graph = layout_diagram(diagram)
    nodes = by_id(graph)
    routed = {edge.edge_id: edge for edge in graph.edges}

    assert nodes["issues"].layer == nodes["projects"].layer == nodes["identity"].layer == nodes["audit"].layer == 1
    assert routed["http-issues"].points[:2] != routed["http-projects"].points[:2]
    assert routed["http-identity"].points[:2] != routed["http-audit"].points[:2]
    assert routed["http-issues"].points[:2] != routed["http-identity"].points[:2]
    assert_edges_avoid_unrelated_nodes(graph)


def test_mixed_roles_to_same_target_use_distinct_target_lanes():
    diagram = {
        "diagram_version": "archbro.diagram.v1",
        "architecture_version": 26,
        "nodes": [{"id": node_id} for node_id in ("delivery", "web", "realtime", "data")],
        "edges": [
            {"id": "web-realtime", "source": "web", "target": "realtime", "layout_role": "BACKBONE"},
            {"id": "realtime-data", "source": "realtime", "target": "data", "layout_role": "BACKBONE"},
            {"id": "delivery-data", "source": "delivery", "target": "data", "layout_role": "CROSS_CUTTING"},
        ],
    }
    graph = layout_diagram(diagram)
    routed = {edge.edge_id: edge for edge in graph.edges}
    backbone = routed["realtime-data"]
    cross = routed["delivery-data"]

    assert backbone.points[-1] != cross.points[-1]
    assert overlap_length_for_test(backbone.points[-2:], cross.points[-2:]) == 0


def overlap_length_for_test(first_points, second_points):
    a1, a2 = first_points
    b1, b2 = second_points
    if a1.y == a2.y == b1.y == b2.y:
        return max(0.0, min(max(a1.x,a2.x),max(b1.x,b2.x))-max(min(a1.x,a2.x),min(b1.x,b2.x)))
    if a1.x == a2.x == b1.x == b2.x:
        return max(0.0, min(max(a1.y,a2.y),max(b1.y,b2.y))-max(min(a1.y,a2.y),min(b1.y,b2.y)))
    return 0.0


def test_incoming_and_outgoing_corridors_use_their_own_endpoint_offsets():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "architecture_version": 27,
            "nodes": [{"id": node_id} for node_id in ("event", "presence", "websocket")],
            "edges": [
                {"id": "event-websocket", "source": "event", "target": "websocket", "layout_role": "BACKBONE"},
                {"id": "websocket-presence", "source": "websocket", "target": "presence", "layout_role": "BACKBONE"},
                {"id": "presence-websocket", "source": "presence", "target": "websocket", "layout_role": "BACKBONE"},
            ],
        }
    )
    routed = {edge.edge_id: edge for edge in graph.edges}
    incoming = routed["event-websocket"]
    outgoing = routed["websocket-presence"]

    # The incoming target approach and outgoing source departure touch the same
    # node side, but their distinct endpoint ports must own distinct x lanes.
    assert incoming.points[-2].x != outgoing.points[1].x
    assert outgoing.points[1].x < incoming.points[-2].x


def test_frontend_uses_archify_rounded_path_radius_eight():
    app = (Path(__file__).resolve().parents[1] / "frontend" / "web" / "app.js").read_text(encoding="utf-8")
    assert "function graphPathData(points = [], radius = 8)" in app
    assert "commands.push(`Q ${current.x} ${current.y} ${after.x} ${after.y}`);" in app


def test_adjacent_backward_route_uses_archify_simplification_without_micro_u_turn():
    graph = layout_diagram(
        {
            "diagram_version": "archbro.diagram.v1",
            "architecture_version": 28,
            "nodes": [{"id": node_id} for node_id in ("web", "application", "persistence")],
            "edges": [
                {"id": "web-app", "source": "web", "target": "application", "layout_role": "BACKBONE"},
                {"id": "app-db", "source": "application", "target": "persistence", "layout_role": "BACKBONE"},
                {"id": "db-app", "source": "persistence", "target": "application", "layout_role": "CROSS_CUTTING"},
            ],
        }
    )
    routed = {edge.edge_id: edge for edge in graph.edges}
    reverse = routed["db-app"]

    assert reverse.routing == "straight"
    assert len(reverse.points) == 2
    assert reverse.points[0].y == reverse.points[-1].y
    assert_endpoint_side_contract(graph)
    assert_route_rhythm(reverse)


def test_living_graph_relationships_use_solid_merged_visual_connections():
    root = Path(__file__).resolve().parents[1]
    css = (root / "frontend" / "web" / "styles.css").read_text(encoding="utf-8")
    app = (root / "frontend" / "web" / "app.js").read_text(encoding="utf-8")
    assert '.graph-edge.layout-cross-cutting path' not in css
    assert 'function graphVisualConnections(edges = [], nodes = [])' in app
    assert 'const key = JSON.stringify(pair);' in app
    assert 'const key = `${pair[0]}::${pair[1]}`;' not in app
    assert 'const leftProvenance = Array.isArray(left.provenance) ? left.provenance.length : 0;' in app
    assert "layout_role: 'BACKBONE'" in app
    assert 'memberIds: members.map((edge) => edge.id)' in app
    assert 'bidirectional: directions.size > 1' in app
    assert 'marker-start="url(#arrow-backbone)"' in app
    assert 'arrow-cross-cutting' not in app


def test_scoped_graph_meta_distinguishes_child_links_from_boundary_relationships():
    app = (Path(__file__).resolve().parents[1] / "frontend" / "web" / "app.js").read_text(encoding="utf-8")
    assert 'direct child relationship${display.edges.length===1' in app
    assert 'boundary relationship${boundaryRelationshipCount===1' in app
    assert 'kept at scope boundary' in app
    assert 'Direct children · no projected links' not in app
