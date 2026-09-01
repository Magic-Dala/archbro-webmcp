from __future__ import annotations


from fastapi import FastAPI
from fastapi.testclient import TestClient

from archbro.backend.api.routes import build_router
from archbro.backend.core.contracts import Architecture, Component, Project, Relationship
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database

pytestmark = requires_database


def make_client(dsn) -> tuple[PostgresProjectRepository, TestClient]:
    repository = PostgresProjectRepository(dsn)
    app = FastAPI()
    app.include_router(build_router(repository, FakeModelProvider()))
    return repository, TestClient(app)


def save_hierarchical_fixture(repository: PostgresProjectRepository) -> Project:
    project = Project(
        name="Diagram API",
        goal="Expose scoped authored architecture semantics with deterministic layout.",
        architecture_version=9,
    )
    repository.save_project(project)
    repository.save_architecture(
        project.id,
        Architecture(
            version=9,
            summary="Outside-in API fixture",
            components=[
                Component(
                    id="web",
                    name="Frontend Experience",
                    type="web",
                    responsibility="Own user interaction",
                    children=[Component(id="ui", name="UI", type="ui", responsibility="Render UI")],
                ),
                Component(
                    id="backend",
                    name="Backend",
                    type="backend",
                    responsibility="Own domain services",
                    children=[
                        Component(
                            id="api",
                            name="API",
                            type="service",
                            responsibility="Serve requests",
                            children=[Component(id="validator", name="Validator", type="service", responsibility="Validate requests")],
                        ),
                        Component(id="worker", name="Worker", type="service", responsibility="Run background work"),
                    ],
                ),
                Component(
                    id="data",
                    name="Data",
                    type="data",
                    responsibility="Own durable state",
                    children=[Component(id="db", name="DB", type="db", responsibility="Persist state")],
                ),
                Component(
                    id="external",
                    name="External",
                    type="external",
                    responsibility="Own external integration",
                    children=[Component(id="search", name="Search", type="external_service", responsibility="Search external data")],
                ),
            ],
            relationships=[
                Relationship(source="ui", target="api", relationship_type="HTTPS", description="Submit request"),
                Relationship(source="validator", target="db", relationship_type="SQL", description="Persist validated data"),
                Relationship(source="worker", target="db", relationship_type="SQL", description="Persist background data"),
                Relationship(source="validator", target="search", relationship_type="HTTPS", description="Search during validation"),
                Relationship(source="worker", target="search", relationship_type="EVENT", description="Dispatch search work"),
            ],
        ),
    )
    return project


def assert_matching_graph_contracts(body: dict) -> None:
    diagram = body["diagram"]
    positioned = body["positioned_graph"]
    assert diagram["diagram_version"] == "archbro.diagram.v1"
    assert positioned["layout_version"] == "archbro.layout.v1"
    assert diagram["architecture_version"] == positioned["architecture_version"]
    assert {node["id"] for node in diagram["nodes"]} == {node["node_id"] for node in positioned["nodes"]}
    assert {edge["id"] for edge in diagram["edges"]} == {edge["edge_id"] for edge in positioned["edges"]}


def test_architecture_diagram_endpoint_returns_frozen_root_envelope_and_layout(dsn):
    repository, client = make_client(dsn)
    project = save_hierarchical_fixture(repository)
    response = client.get(f"/projects/{project.id}/architecture/diagram")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema", "project_id", "architecture_version", "scope", "diagram", "positioned_graph"}
    assert body["schema"] == "archbro.scoped_diagram.v1"
    assert body["project_id"] == project.id
    assert body["architecture_version"] == 9
    assert body["scope"] == {
        "component_id": None,
        "node_id": None,
        "label": "Overview",
        "is_leaf": False,
        "ancestor_path": [],
        "direct_relationships": [],
    }
    assert [node["component_id"] for node in body["diagram"]["nodes"]] == ["backend", "data", "external", "web"]
    assert_matching_graph_contracts(body)


def test_architecture_diagram_scoped_route_returns_primary_children_context_and_provenance(dsn):
    repository, client = make_client(dsn)
    project = save_hierarchical_fixture(repository)
    response = client.get(
        f"/projects/{project.id}/architecture/diagram",
        params={"scope": "backend", "expected_architecture_version": 9},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["component_id"] == "backend"
    assert body["scope"]["node_id"] == "node:backend"
    assert body["scope"]["is_leaf"] is False
    assert body["scope"]["ancestor_path"] == [
        {"component_id": "backend", "node_id": "node:backend", "label": "Backend"}
    ]
    roles = {node["component_id"]: node["projection_role"] for node in body["diagram"]["nodes"]}
    assert roles == {
        "api": "PRIMARY",
        "worker": "PRIMARY",
        "data": "CONTEXT",
        "external": "CONTEXT",
        "web": "CONTEXT",
    }
    sql = next(
        edge
        for edge in body["diagram"]["edges"]
        if edge["source"] == "node:api" and edge["target"] == "node:data" and edge["semantic_type"] == "SQL"
    )
    assert sql["projection_kind"] == "DERIVED_CROSSING"
    assert sql["provenance"][0]["source_component_id"] == "validator"
    assert sql["provenance"][0]["target_component_id"] == "db"
    assert_matching_graph_contracts(body)


def test_architecture_diagram_leaf_scope_is_empty_but_reports_scope_truth(dsn):
    repository, client = make_client(dsn)
    project = save_hierarchical_fixture(repository)
    response = client.get(
        f"/projects/{project.id}/architecture/diagram",
        params={"scope": "validator", "expected_architecture_version": 9},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["is_leaf"] is True
    assert [entry["component_id"] for entry in body["scope"]["ancestor_path"]] == ["backend", "api", "validator"]
    assert body["diagram"]["nodes"] == []
    assert body["diagram"]["edges"] == []
    assert body["positioned_graph"]["nodes"] == []
    assert body["positioned_graph"]["edges"] == []
    assert_matching_graph_contracts(body)


def test_architecture_diagram_unknown_scope_fails_closed(dsn):
    repository, client = make_client(dsn)
    project = save_hierarchical_fixture(repository)
    response = client.get(f"/projects/{project.id}/architecture/diagram", params={"scope": "missing"})
    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "architecture_node_not_found", "component_id": "missing"}


def test_architecture_diagram_stale_version_fails_before_scope_projection(dsn):
    repository, client = make_client(dsn)
    project = save_hierarchical_fixture(repository)
    response = client.get(
        f"/projects/{project.id}/architecture/diagram",
        params={"scope": "missing", "expected_architecture_version": 8},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stale_architecture_version",
        "expected_architecture_version": 8,
        "current_architecture_version": 9,
    }


def test_flat_legacy_architecture_remains_readable_through_root_api(dsn):
    repository, client = make_client(dsn)
    project = Project(name="Flat", goal="Keep legacy topology readable.", architecture_version=2)
    repository.save_project(project)
    repository.save_architecture(
        project.id,
        Architecture(
            version=2,
            components=[
                Component(id="ui", name="UI", type="ui", responsibility="UI"),
                Component(id="api", name="API", type="api", responsibility="API"),
                Component(id="db", name="DB", type="db", responsibility="DB"),
            ],
            relationships=[
                Relationship(source="ui", target="api", relationship_type="HTTPS"),
                Relationship(source="api", target="db", relationship_type="SQL"),
            ],
        ),
    )
    response = client.get(f"/projects/{project.id}/architecture/diagram")
    assert response.status_code == 200
    body = response.json()
    assert {node["component_id"] for node in body["diagram"]["nodes"]} == {"ui", "api", "db"}
    assert all(node["child_count"] == 0 for node in body["diagram"]["nodes"])
    assert all(edge["projection_kind"] == "AUTHORED" for edge in body["diagram"]["edges"])
    assert all(edge["id"].startswith("edge:") for edge in body["diagram"]["edges"])
    assert_matching_graph_contracts(body)
