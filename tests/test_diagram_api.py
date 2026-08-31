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


def test_architecture_diagram_endpoint_returns_authorized_renderer_envelope(dsn):
    repository, client = make_client(dsn)
    project = Project(
        name="Diagram API",
        goal="Expose authored architecture semantics with deterministic layout.",
    )
    repository.save_project(project)
    architecture = Architecture(
        version=4,
        summary="API to deterministic layout",
        components=[
            Component(id="api", name="API", type="fastapi", responsibility="Serve architecture data"),
            Component(id="store", name="Store", type="firestore", responsibility="Persist canonical architecture"),
        ],
        relationships=[
            Relationship(
                source="api",
                target="store",
                relationship_type="READ_WRITE",
                description="Persist and load architecture",
            )
        ],
    )
    repository.save_architecture(project.id, architecture)

    response = client.get(f"/projects/{project.id}/architecture/diagram")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"diagram", "positioned_graph"}
    diagram = body["diagram"]
    positioned = body["positioned_graph"]
    assert diagram["diagram_version"] == "archbro.diagram.v1"
    assert positioned["layout_version"] == "archbro.layout.v1"
    assert diagram["architecture_version"] == positioned["architecture_version"] == 4
    assert [node["id"] for node in diagram["nodes"]] == ["node:api", "node:store"]
    assert positioned["stable_order"] == ["node:api", "node:store"]
    assert positioned["edges"][0]["source"] == "node:api"
    assert positioned["edges"][0]["target"] == "node:store"
    assert len(positioned["edges"][0]["points"]) >= 2
