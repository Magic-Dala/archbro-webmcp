import pytest
from pydantic import ValidationError

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    ArchitectureOption,
    Component,
    Relationship,
    Task,
    TaskStatus,
)
from archbro.backend.core.diagram import DiagramHealth, project_diagram


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


def test_same_input_produces_same_diagram_ir_and_stable_ids():
    architecture = make_architecture()

    first = project_diagram(architecture)
    second = project_diagram(architecture.model_copy(deep=True))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert [node.id for node in first.nodes] == [
        "node:platform",
        "node:api",
        "node:store",
    ]
    assert len({edge.id for edge in first.edges}) == len(first.edges)
    assert [edge.id for edge in first.edges] == [edge.id for edge in second.edges]


def test_hierarchy_semantics_and_architecture_version_are_preserved():
    view = project_diagram(make_architecture())
    nodes = {node.component_id: node for node in view.nodes}

    assert view.diagram_version == "archbro.diagram.v1"
    assert view.architecture_version == 7
    assert view.summary == "Deterministic architecture projection"
    assert nodes["platform"].parent_id is None
    assert nodes["platform"].depth == 1
    assert nodes["api"].parent_id == "node:platform"
    assert nodes["api"].depth == 2
    assert nodes["store"].parent_id == "node:api"
    assert nodes["store"].depth == 3
    assert nodes["api"].semantic_kind == ArchitectureNodeKind.SERVICE
    assert nodes["api"].semantic_type == "fastapi"
    assert nodes["api"].label == "API"
    assert nodes["api"].responsibility == "Serve backend requests"
    assert {(edge.source, edge.target) for edge in view.edges} == {
        ("node:api", "node:store"),
        ("node:platform", "node:api"),
    }


def test_dangling_relationship_fails_closed_before_renderer_boundary():
    with pytest.raises(
        ValidationError, match="relationships must reference existing node ids"
    ):
        Architecture(
            components=[
                Component(id="api", name="API", type="service", responsibility="API")
            ],
            relationships=[
                Relationship(
                    source="api",
                    target="missing",
                    relationship_type="CALLS",
                )
            ],
        )

    unsafe = Architecture.model_construct(
        version=1,
        summary="Bypassed validation fixture",
        components=[
            Component(id="api", name="API", type="service", responsibility="API")
        ],
        relationships=[
            Relationship(
                source="api",
                target="missing",
                relationship_type="CALLS",
            )
        ],
        decisions=[],
        assumptions=[],
        risks=[],
    )

    with pytest.raises(ValueError, match="diagram projection rejects dangling relationship"):
        project_diagram(unsafe)


def test_task_and_pending_proposal_status_are_projection_only():
    architecture = make_architecture()
    before = architecture.model_dump(mode="json")
    tasks = [
        Task(
            id="task-api",
            title="Implement request handling",
            status=TaskStatus.IN_PROGRESS,
            related_component="api",
        )
    ]
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
    assert nodes["api"].supporting_text == [
        "Task IN_PROGRESS: Implement request handling"
    ]
    assert nodes["store"].status.proposal_status.value == "PENDING"
    assert nodes["store"].status.health == DiagramHealth.CHANGE_PENDING
    assert nodes["store"].supporting_text == [
        "Pending change: Evaluate a different persistence boundary."
    ]
    assert proposals[0].status.value == "PENDING"
    assert architecture.model_dump(mode="json") == before


def test_canonical_architecture_has_no_presentation_state():
    presentation_fields = {
        "x",
        "y",
        "position",
        "color",
        "zoom",
        "layout",
        "viewer_state",
        "selected",
    }

    assert presentation_fields.isdisjoint(Architecture.model_fields)
    assert presentation_fields.isdisjoint(Component.model_fields)
