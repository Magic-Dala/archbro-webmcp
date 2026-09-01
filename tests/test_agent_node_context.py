from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from archbro.backend.agent.node_context import (
    ArchitectureNodeNotFoundError,
    build_node_context,
    find_architecture_path,
)
from archbro.backend.core.contracts import Architecture, Component, Project, Relationship
from archbro.backend.core.diagram import _edge_id
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database


def _architecture() -> Architecture:
    return Architecture(
        version=7,
        components=[
            Component(
                id="backend",
                name="Backend",
                type="system",
                responsibility="Own backend processing",
                children=[
                    Component(id="api", name="API", type="service", responsibility="Serve requests"),
                    Component(id="worker", name="Worker", type="service", responsibility="Run jobs"),
                ],
            ),
            Component(
                id="data",
                name="Data",
                type="system",
                responsibility="Own durable state",
                children=[
                    Component(id="db", name="DB", type="database", responsibility="Persist state"),
                ],
            ),
            Component(
                id="external",
                name="External",
                type="system",
                responsibility="Own external dependencies",
                children=[
                    Component(id="search", name="Search", type="api", responsibility="External search"),
                ],
            ),
        ],
        relationships=[
            Relationship(source="api", target="db", relationship_type="SQL", description="Read/write state"),
            Relationship(source="api", target="search", relationship_type="HTTPS", description="Query search"),
            Relationship(source="worker", target="api", relationship_type="CALL", description="Submit work"),
            Relationship(source="db", target="worker", relationship_type="EVENT", description="Wake worker"),
        ],
    )


def test_node_context_uses_authored_direction_hierarchy_and_shared_relationship_identity():
    architecture = _architecture()
    body = build_node_context(
        architecture,
        "project-1",
        "node:api",
        direction="downstream",
        max_hops=1,
    )

    assert body["schema"] == "archbro.node_context.v1"
    assert body["origin"]["component_id"] == "api"
    assert body["scope"] == {
        "root_node_id": "node:backend",
        "parent_node_id": "node:backend",
        "child_node_ids": [],
    }
    assert [node["node_id"] for node in body["nodes"]] == ["node:db", "node:search"]
    assert all(node["matched_directions"] == ["downstream"] for node in body["nodes"])
    relationships = {item["relationship_type"]: item for item in body["relationships"]}
    sql = next(item for item in architecture.relationships if item.relationship_type == "SQL")
    assert relationships["SQL"]["id"] == _edge_id(sql, 0)
    assert relationships["SQL"]["provenance"] == {
        "kind": "CANONICAL_RELATIONSHIP",
        "architecture_version": 7,
        "relationship_id": _edge_id(sql, 0),
        "source_component_id": "api",
        "target_component_id": "db",
        "source_node_id": "node:api",
        "target_node_id": "node:db",
        "semantic_type": "SQL",
        "supporting_text": "Read/write state",
    }
    assert body["truncated"] is True
    assert body["limit_reason"] == "MAX_HOPS"


def test_both_is_union_of_independent_cycle_safe_directional_bfs_and_result_bound_is_explicit():
    body = build_node_context(
        _architecture(),
        "project-1",
        "node:api",
        direction="both",
        max_hops=2,
        max_results=40,
    )
    by_id = {node["node_id"]: node for node in body["nodes"]}
    assert by_id["node:worker"]["matched_directions"] == ["upstream", "downstream"]
    assert by_id["node:db"]["matched_directions"] == ["upstream", "downstream"]
    assert by_id["node:search"]["matched_directions"] == ["downstream"]
    assert "node:api" not in by_id
    assert body["counts"]["nodes"] == 3

    clipped = build_node_context(
        _architecture(),
        "project-1",
        "node:api",
        direction="both",
        max_hops=2,
        max_results=1,
    )
    assert clipped["counts"]["nodes"] == 1
    assert clipped["truncated"] is True
    assert clipped["limit_reason"] == "MAX_RESULTS"


def test_directed_path_found_unreachable_limit_and_unknown_are_fail_closed():
    architecture = _architecture()
    found = find_architecture_path(architecture, "project-1", "node:api", "node:db")
    assert found["status"] == "FOUND"
    assert found["hops"] == 1
    assert [node["node_id"] for node in found["nodes"]] == ["node:api", "node:db"]
    assert found["relationships"][0]["relationship_type"] == "SQL"

    unreachable = find_architecture_path(architecture, "project-1", "node:search", "node:api")
    assert unreachable["status"] == "UNREACHABLE"
    assert unreachable["relationships"] == []

    limited = find_architecture_path(
        architecture,
        "project-1",
        "node:api",
        "node:worker",
        max_hops=1,
    )
    assert limited["status"] == "LIMIT_REACHED"

    same = find_architecture_path(architecture, "project-1", "node:api", "node:api", max_hops=0)
    assert same["status"] == "FOUND"
    assert same["hops"] == 0
    assert same["relationships"] == []

    with pytest.raises(ArchitectureNodeNotFoundError):
        build_node_context(architecture, "project-1", "node:stale")


def _api_client(dsn: str) -> tuple[TestClient, str]:
    repository = PostgresProjectRepository(dsn)
    project = Project(name="Node Context", goal="Test bounded authored reachability")
    repository.save_project(project)
    repository.save_architecture(project.id, _architecture())
    return TestClient(build_app(repository, FakeModelProvider())), project.id


@requires_database
def test_agent_surface_node_context_path_stale_unknown_and_query_validation(dsn):
    client, project_id = _api_client(dsn)

    context = client.get(
        f"/projects/{project_id}/architecture/nodes/node:api/context",
        params={"direction": "upstream", "max_hops": 2, "max_results": 10},
    )
    assert context.status_code == 200
    assert context.json()["query"]["direction"] == "upstream"

    path = client.get(
        f"/projects/{project_id}/architecture/path",
        params={"source_id": "node:api", "target_id": "node:db", "max_hops": 8},
    )
    assert path.status_code == 200
    assert path.json()["schema"] == "archbro.architecture_path.v1"

    stale = client.get(
        f"/projects/{project_id}/architecture/nodes/node:api/context",
        params={"expected_architecture_version": 6},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "error": "stale_architecture_version",
        "expected_architecture_version": 6,
        "current_architecture_version": 7,
    }

    unknown = client.get(f"/projects/{project_id}/architecture/nodes/node:missing/context")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["error"] == "architecture_node_not_found"

    invalid = client.get(
        f"/projects/{project_id}/architecture/nodes/node:api/context",
        params={"max_hops": 9},
    )
    assert invalid.status_code == 422
