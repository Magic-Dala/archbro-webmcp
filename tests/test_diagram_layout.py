from __future__ import annotations

from itertools import combinations

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
