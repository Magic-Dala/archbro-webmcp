import pytest
from pydantic import ValidationError

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    ArchitectureOption,
    Component,
    ProposalStatus,
    Relationship,
    Task,
    TaskStatus,
)
from archbro.backend.core.diagram import (
    DiagramEdgeLayoutRole,
    DiagramEdgeProjectionKind,
    DiagramHealth,
    DiagramProjectionRole,
    project_diagram,
    project_scoped_diagram,
)


def make_architecture() -> Architecture:
    return Architecture(
        version=7,
        summary="Deterministic architecture projection",
        components=[
            Component(
                id="platform",
                name="Platform",
                type="system_boundary",
                kind=ArchitectureNodeKind.SYSTEM,
                responsibility="Own the product runtime boundary",
                children=[
                    Component(
                        id="api",
                        name="API",
                        type="fastapi",
                        kind=ArchitectureNodeKind.SERVICE,
                        responsibility="Serve backend requests",
                        children=[
                            Component(
                                id="store",
                                name="Store",
                                type="firestore",
                                kind=ArchitectureNodeKind.DATA_STORE,
                                responsibility="Persist canonical state",
                            )
                        ],
                    )
                ],
            )
        ],
        relationships=[
            Relationship(
                source="api",
                target="store",
                relationship_type="READ_WRITE",
                description="Persist and load state",
            ),
            Relationship(
                source="platform",
                target="api",
                relationship_type="CONTAINS_RUNTIME",
                description="Hosts the API runtime",
            ),
        ],
    )


def hierarchical_fixture(*, reverse: bool = False) -> Architecture:
    web = Component(
        id="web",
        name="Frontend Experience",
        type="web",
        responsibility="Own user-facing interaction",
        children=[Component(id="ui", name="UI", type="ui", responsibility="Render the product")],
    )
    backend = Component(
        id="backend",
        name="Backend / Domain Services",
        type="backend",
        responsibility="Own domain processing",
        children=[
            Component(
                id="api",
                name="API",
                type="service",
                responsibility="Serve requests",
                children=[
                    Component(
                        id="validator",
                        name="Validator",
                        type="service",
                        responsibility="Validate domain requests",
                    )
                ],
            ),
            Component(id="worker", name="Worker", type="service", responsibility="Run background work"),
        ],
    )
    data = Component(
        id="data",
        name="Data & State",
        type="data",
        responsibility="Own durable state",
        children=[Component(id="db", name="Database", type="database", responsibility="Persist state")],
    )
    external = Component(
        id="external",
        name="External Integrations",
        type="external",
        responsibility="Own external integrations",
        children=[
            Component(
                id="search",
                name="Search",
                type="external_service",
                responsibility="Search external data",
            )
        ],
    )
    components = [web, backend, data, external]
    relationships = [
        Relationship(source="ui", target="api", relationship_type="HTTPS", description="Submit user requests"),
        Relationship(source="validator", target="db", relationship_type="SQL", description="Persist validated state"),
        Relationship(source="worker", target="db", relationship_type="SQL", description="Persist background results"),
        Relationship(source="validator", target="search", relationship_type="HTTPS", description="Validate with external search"),
        Relationship(source="worker", target="search", relationship_type="EVENT", description="Dispatch external search work"),
    ]
    if reverse:
        components = list(reversed(components))
        for component in components:
            component.children = list(reversed(component.children))
            for child in component.children:
                child.children = list(reversed(child.children))
        relationships = list(reversed(relationships))
    return Architecture(version=9, summary="Outside-in fixture", components=components, relationships=relationships)


def test_same_input_produces_same_diagram_ir_and_stable_ids():
    architecture = make_architecture()
    first = project_diagram(architecture)
    second = project_diagram(architecture.model_copy(deep=True))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert [node.id for node in first.nodes] == ["node:platform", "node:api", "node:store"]
    assert len({edge.id for edge in first.edges}) == len(first.edges)
    assert [edge.id for edge in first.edges] == [edge.id for edge in second.edges]
    assert all(edge.projection_kind == DiagramEdgeProjectionKind.AUTHORED for edge in first.edges)
    assert all(len(edge.provenance) == 1 for edge in first.edges)


def test_hierarchy_semantics_and_architecture_version_are_preserved():
    view = project_diagram(make_architecture())
    nodes = {node.component_id: node for node in view.nodes}
    assert view.diagram_version == "archbro.diagram.v1"
    assert view.architecture_version == 7
    assert view.summary == "Deterministic architecture projection"
    assert nodes["platform"].parent_id is None
    assert nodes["platform"].depth == 1
    assert nodes["platform"].child_count == 1
    assert nodes["api"].parent_id == "node:platform"
    assert nodes["api"].depth == 2
    assert nodes["api"].child_count == 1
    assert nodes["store"].parent_id == "node:api"
    assert nodes["store"].depth == 3
    assert nodes["store"].child_count == 0
    assert nodes["api"].semantic_kind == ArchitectureNodeKind.SERVICE
    assert nodes["api"].semantic_type == "fastapi"
    assert nodes["api"].label == "API"
    assert nodes["api"].responsibility == "Serve backend requests"
    assert {(edge.source, edge.target) for edge in view.edges} == {
        ("node:api", "node:store"),
        ("node:platform", "node:api"),
    }


def test_dangling_relationship_fails_closed_before_renderer_boundary():
    with pytest.raises(ValidationError, match="relationships must reference existing node ids"):
        Architecture(
            components=[Component(id="api", name="API", type="service", responsibility="API")],
            relationships=[Relationship(source="api", target="missing", relationship_type="CALLS")],
        )
    unsafe = Architecture.model_construct(
        version=1,
        summary="Bypassed validation fixture",
        components=[Component(id="api", name="API", type="service", responsibility="API")],
        relationships=[Relationship(source="api", target="missing", relationship_type="CALLS")],
        decisions=[],
        assumptions=[],
        risks=[],
    )
    with pytest.raises(ValueError, match="diagram projection rejects dangling relationship"):
        project_diagram(unsafe)


def test_task_and_pending_proposal_status_are_projection_only():
    architecture = make_architecture()
    before = architecture.model_dump(mode="json")
    tasks = [Task(id="task-api", title="Implement request handling", status=TaskStatus.IN_PROGRESS, related_component="api")]
    proposals = [
        ArchitectureChangeProposal(
            id="proposal-store",
            project_id="project-test",
            reason="Evaluate a different persistence boundary.",
            evidence=["Human requested an architecture review."],
            observed_change="Persistence requirements may change.",
            affected_components=["store"],
            impact="Storage implementation may change after approval.",
            recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
        )
    ]
    view = project_diagram(architecture, tasks=tasks, proposals=proposals)
    nodes = {node.component_id: node for node in view.nodes}
    assert nodes["api"].status.task_status == TaskStatus.IN_PROGRESS
    assert nodes["api"].status.health == DiagramHealth.IN_PROGRESS
    assert nodes["api"].supporting_text == ["Task IN_PROGRESS: Implement request handling"]
    assert nodes["store"].status.proposal_status.value == "PENDING"
    assert nodes["store"].status.health == DiagramHealth.CHANGE_PENDING
    assert nodes["store"].supporting_text == ["Pending change: Evaluate a different persistence boundary."]
    assert proposals[0].status.value == "PENDING"
    assert architecture.model_dump(mode="json") == before


def test_scoped_projection_aggregates_descendant_execution_and_review_health_without_mutating_canonical_state():
    architecture = hierarchical_fixture()
    before = architecture.model_dump(mode="json")
    tasks = [
        Task(
            id="task-validator",
            title="Unblock validator",
            status=TaskStatus.BLOCKED,
            related_component="validator",
        )
    ]
    proposals = [
        ArchitectureChangeProposal(
            id="proposal-db",
            project_id="project-test",
            reason="Review database boundary",
            evidence=["Database requirements changed."],
            observed_change="Database boundary may change.",
            affected_components=["db"],
            impact="Data boundary review required.",
            recommended_option=ArchitectureOption.KEEP_CURRENT,
        )
    ]

    root = project_scoped_diagram(architecture, tasks=tasks, proposals=proposals)
    root_nodes = {node.component_id: node for node in root.diagram.nodes}
    assert root_nodes["backend"].status.task_status == TaskStatus.BLOCKED
    assert root_nodes["backend"].status.health == DiagramHealth.BLOCKED
    assert "Task BLOCKED: Unblock validator" in root_nodes["backend"].supporting_text
    assert root_nodes["data"].status.proposal_status == ProposalStatus.PENDING
    assert root_nodes["data"].status.health == DiagramHealth.CHANGE_PENDING

    backend_scope = project_scoped_diagram(
        architecture,
        scope_component_id="backend",
        tasks=tasks,
        proposals=proposals,
    )
    backend_nodes = {node.component_id: node for node in backend_scope.diagram.nodes}
    assert backend_nodes["api"].status.task_status == TaskStatus.BLOCKED
    assert backend_nodes["api"].status.health == DiagramHealth.BLOCKED
    assert architecture.model_dump(mode="json") == before


def test_root_projection_is_four_boundaries_with_truthful_aggregate_provenance():
    root = project_scoped_diagram(hierarchical_fixture())
    assert root.schema == "archbro.scoped_diagram.v1"
    assert root.scope.component_id is None
    assert [node.component_id for node in root.diagram.nodes] == ["backend", "data", "external", "web"]
    assert all(node.projection_role == DiagramProjectionRole.PRIMARY for node in root.diagram.nodes)
    assert all(node.depth == 1 for node in root.diagram.nodes)
    edges = {(edge.source, edge.target, edge.semantic_type): edge for edge in root.diagram.edges}
    assert set(edges) == {
        ("node:web", "node:backend", "HTTPS"),
        ("node:backend", "node:data", "SQL"),
        ("node:backend", "node:external", "MULTIPLE"),
    }
    sql = edges[("node:backend", "node:data", "SQL")]
    assert sql.projection_kind == DiagramEdgeProjectionKind.DERIVED_CROSSING
    assert len(sql.provenance) == 2
    assert {item.source_component_id for item in sql.provenance} == {"validator", "worker"}
    assert [item.relationship_id for item in sql.provenance] == sorted(item.relationship_id for item in sql.provenance)
    crossing = edges[("node:backend", "node:external", "MULTIPLE")]
    assert crossing.label == "2 relationships"
    assert {item.semantic_type for item in crossing.provenance} == {"HTTPS", "EVENT"}


def test_operational_relationships_are_cross_cutting_but_product_flow_remains_backbone():
    architecture = Architecture(
        components=[
            Component(id="delivery", name="Delivery", type="infrastructure", responsibility="Operate the system"),
            Component(id="web", name="Web", type="ui", responsibility="Serve users"),
            Component(id="api", name="API", type="service", responsibility="Serve requests"),
        ],
        relationships=[
            Relationship(source="delivery", target="api", relationship_type="RUNS", description="Runs the API"),
            Relationship(source="delivery", target="web", relationship_type="VERIFIES", description="Verifies the web build"),
            Relationship(source="web", target="api", relationship_type="QUERIES_AND_COMMANDS", description="Calls the API"),
        ],
    )

    root = project_scoped_diagram(architecture)
    roles = {(edge.source, edge.target): edge.layout_role for edge in root.diagram.edges}

    assert roles[("node:delivery", "node:api")] == DiagramEdgeLayoutRole.CROSS_CUTTING
    assert roles[("node:delivery", "node:web")] == DiagramEdgeLayoutRole.CROSS_CUTTING
    assert roles[("node:web", "node:api")] == DiagramEdgeLayoutRole.BACKBONE


def test_component_scope_shows_only_scope_and_immediate_children_while_preserving_boundary_provenance():
    scoped = project_scoped_diagram(hierarchical_fixture(), scope_component_id="backend")
    assert scoped.scope.component_id == "backend"
    assert scoped.scope.node_id == "node:backend"
    assert scoped.scope.is_leaf is False
    assert [entry.component_id for entry in scoped.scope.ancestor_path] == ["backend"]
    roles = {node.component_id: node.projection_role for node in scoped.diagram.nodes}
    assert roles == {
        "backend": DiagramProjectionRole.SCOPE,
        "api": DiagramProjectionRole.PRIMARY,
        "worker": DiagramProjectionRole.PRIMARY,
    }
    assert "validator" not in roles
    scope_node = next(node for node in scoped.diagram.nodes if node.component_id == "backend")
    assert scope_node.parent_id is None
    assert scope_node.child_count == 2
    assert next(node for node in scoped.diagram.nodes if node.component_id == "api").parent_id == "node:backend"
    assert next(node for node in scoped.diagram.nodes if node.component_id == "worker").parent_id == "node:backend"
    assert next(node for node in scoped.diagram.nodes if node.component_id == "api").child_count == 1
    assert scoped.diagram.edges == []
    assert len(scoped.scope.direct_relationships) == 5
    assert {item.source_component_id for item in scoped.scope.direct_relationships} == {"ui", "validator", "worker"}


def test_api_scope_and_leaf_scope_do_not_fabricate_deeper_topology():
    architecture = hierarchical_fixture()
    api_scope = project_scoped_diagram(architecture, scope_component_id="api")
    roles = {node.component_id: node.projection_role for node in api_scope.diagram.nodes}
    assert roles["api"] == DiagramProjectionRole.SCOPE
    assert roles["validator"] == DiagramProjectionRole.PRIMARY
    assert next(node for node in api_scope.diagram.nodes if node.component_id == "validator").parent_id == "node:api"
    assert set(roles) == {"api", "validator"}
    assert api_scope.diagram.edges == []
    assert len(api_scope.scope.direct_relationships) == 3
    assert "worker" not in roles
    leaf = project_scoped_diagram(architecture, scope_component_id="validator")
    assert leaf.scope.is_leaf is True
    assert [entry.component_id for entry in leaf.scope.ancestor_path] == ["backend", "api", "validator"]
    assert leaf.diagram.nodes == []
    assert leaf.diagram.edges == []


def test_flat_legacy_root_remains_one_for_one_authored_topology():
    architecture = Architecture(
        version=3,
        components=[
            Component(id="ui", name="UI", type="ui", responsibility="UI"),
            Component(id="api", name="API", type="api", responsibility="API"),
            Component(id="db", name="DB", type="db", responsibility="DB"),
        ],
        relationships=[
            Relationship(source="ui", target="api", relationship_type="HTTPS"),
            Relationship(source="api", target="db", relationship_type="SQL"),
        ],
    )
    root = project_scoped_diagram(architecture)
    assert {node.component_id for node in root.diagram.nodes} == {"ui", "api", "db"}
    assert all(node.child_count == 0 for node in root.diagram.nodes)
    assert all(edge.projection_kind == DiagramEdgeProjectionKind.AUTHORED for edge in root.diagram.edges)
    assert all(edge.id.startswith("edge:") for edge in root.diagram.edges)
    assert not any(edge.id.startswith("agg:") for edge in root.diagram.edges)


def test_scoped_projection_is_stable_under_incidental_canonical_input_ordering():
    first = project_scoped_diagram(hierarchical_fixture())
    second = project_scoped_diagram(hierarchical_fixture(reverse=True))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    first_backend = project_scoped_diagram(hierarchical_fixture(), scope_component_id="backend")
    second_backend = project_scoped_diagram(hierarchical_fixture(reverse=True), scope_component_id="backend")
    assert first_backend.model_dump(mode="json") == second_backend.model_dump(mode="json")


def test_canonical_architecture_has_no_presentation_state():
    presentation_fields = {"x", "y", "position", "color", "zoom", "layout", "viewer_state", "selected"}
    assert presentation_fields.isdisjoint(Architecture.model_fields)
    assert presentation_fields.isdisjoint(Component.model_fields)


def test_generated_relationship_vocabulary_gets_stable_layout_roles():
    from archbro.backend.core.diagram import _layout_role_for_semantic_types

    assert _layout_role_for_semantic_types(["MONITORS"]).value == "CROSS_CUTTING"
    assert _layout_role_for_semantic_types(["VALIDATES_MIGRATIONS"]).value == "CROSS_CUTTING"
    assert _layout_role_for_semantic_types(["AUTHORIZES_WITH"]).value == "CROSS_CUTTING"
    assert _layout_role_for_semantic_types(["MONITORS", "VERIFIES"]).value == "CROSS_CUTTING"
    assert _layout_role_for_semantic_types(["PERSISTS_THROUGH", "APPENDS_ATOMICALLY"]).value == "BACKBONE"
    assert _layout_role_for_semantic_types(["QUERIES", "COMMANDS", "AUTHENTICATES_WITH"]).value == "BACKBONE"
    assert _layout_role_for_semantic_types(["SUBSCRIBES"]).value == "BACKBONE"
    assert _layout_role_for_semantic_types(["TOTALLY_NEW_SUPPORT_RELATION"]).value == "CROSS_CUTTING"


def test_map_collapses_parallel_backbone_semantics_to_one_connection():
    from archbro.backend.core.diagram import map_edge_ids

    architecture = Architecture(
        version=1,
        components=[
            Component(id="web", name="Web", type="ui", responsibility="Serve users"),
            Component(id="api", name="API", type="service", responsibility="Serve requests"),
        ],
        relationships=[
            Relationship(source="web", target="api", relationship_type="CALLS"),
            Relationship(source="web", target="api", relationship_type="PERSISTS"),
        ],
    )

    diagram = project_scoped_diagram(architecture).diagram
    parallel = [
        edge
        for edge in diagram.edges
        if edge.source == "node:web" and edge.target == "node:api"
    ]
    assert len(parallel) == 2
    assert all(edge.layout_role == DiagramEdgeLayoutRole.BACKBONE for edge in parallel)

    selected = map_edge_ids(diagram)
    chosen = [edge for edge in parallel if edge.id in selected]
    assert len(chosen) == 1

    reversed_architecture = architecture.model_copy(
        update={"relationships": list(reversed(architecture.relationships))}
    )
    reversed_diagram = project_scoped_diagram(reversed_architecture).diagram
    assert map_edge_ids(reversed_diagram) == selected


def test_map_keeps_backbone_diamond_and_only_one_cross_cutting_connector():
    from archbro.backend.core.diagram import map_edge_ids

    architecture = Architecture(
        version=1,
        components=[
            Component(id="delivery", name="Delivery", type="infrastructure", responsibility="Deliver and observe"),
            Component(id="web", name="Web", type="ui", responsibility="Serve users"),
            Component(id="realtime", name="Realtime", type="service", responsibility="Coordinate live state"),
            Component(id="app", name="Application", type="service", responsibility="Serve application APIs"),
            Component(id="data", name="Data", type="data_store", responsibility="Persist state"),
        ],
        relationships=[
            Relationship(source="web", target="app", relationship_type="COMMANDS_AND_QUERIES"),
            Relationship(source="web", target="realtime", relationship_type="SUBSCRIBES"),
            Relationship(source="realtime", target="data", relationship_type="CONSUMES"),
            Relationship(source="app", target="data", relationship_type="PERSISTS_THROUGH"),
            Relationship(source="delivery", target="app", relationship_type="VERIFIES"),
            Relationship(source="delivery", target="app", relationship_type="MONITORS"),
            Relationship(source="delivery", target="data", relationship_type="VALIDATES_MIGRATIONS"),
            Relationship(source="delivery", target="realtime", relationship_type="MONITORS"),
            Relationship(source="realtime", target="app", relationship_type="AUTHORIZES_WITH"),
        ],
    )
    diagram = project_scoped_diagram(architecture).diagram
    selected = map_edge_ids(diagram)
    chosen = {(edge.source, edge.target, edge.layout_role.value) for edge in diagram.edges if edge.id in selected}

    assert chosen == {
        ("node:web", "node:app", "BACKBONE"),
        ("node:web", "node:realtime", "BACKBONE"),
        ("node:realtime", "node:data", "BACKBONE"),
        ("node:app", "node:data", "BACKBONE"),
        ("node:delivery", "node:app", "CROSS_CUTTING"),
    }
