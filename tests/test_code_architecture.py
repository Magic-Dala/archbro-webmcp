from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from archbro.backend.agent.code_architecture import (
    CODE_ARCHITECTURE_SCHEMA,
    CodeArchitectureSnapshotRequest,
    build_code_architecture_snapshot,
)
from archbro.backend.core.contracts import ProjectEventType
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database


REVISION = "0123456789abcdef0123456789abcdef01234567"


def snapshot_payload() -> dict:
    return {
        "repository": "Magic-Dala/archbro",
        "revision": REVISION,
        "summary": "Browser workspace calls the project API; the API persists project state.",
        "components": [
            {
                "id": "web",
                "name": "Browser Workspace",
                "type": "frontend",
                "responsibility": "Render the project workspace.",
                "kind": "UI",
                "source_evidence_ids": ["web-entry"],
            },
            {
                "id": "backend",
                "name": "Backend Runtime",
                "type": "backend",
                "responsibility": "Own product APIs and state transitions.",
                "kind": "SYSTEM",
                "source_evidence_ids": ["api-entry"],
                "children": [
                    {
                        "id": "api",
                        "name": "Project API",
                        "type": "service",
                        "responsibility": "Serve project and architecture requests.",
                        "kind": "SERVICE",
                        "source_evidence_ids": ["api-entry"],
                    },
                    {
                        "id": "repo",
                        "name": "Project Repository",
                        "type": "data-access",
                        "responsibility": "Persist canonical project state.",
                        "kind": "DATA_STORE",
                        "source_evidence_ids": ["repo-entry"],
                    },
                ],
            },
        ],
        "relationships": [
            {
                "source": "web",
                "target": "api",
                "relationship_type": "HTTPS",
                "description": "The browser invokes project endpoints.",
                "source_evidence_ids": ["web-entry", "api-entry"],
            },
            {
                "source": "api",
                "target": "repo",
                "relationship_type": "CALL",
                "description": "API handlers delegate persistence to the project repository.",
                "source_evidence_ids": ["api-entry", "repo-entry"],
            },
        ],
        "source_evidence": [
            {
                "id": "web-entry",
                "path": "frontend/web/app.js",
                "line_start": 1,
                "line_end": 2,
                "excerpt": "const state = {};\nasync function api(path) {}",
                "symbol": "api",
            },
            {
                "id": "api-entry",
                "path": "src/archbro/backend/api/routes.py",
                "line_start": 10,
                "line_end": 11,
                "excerpt": "def build_router():\n    pass",
                "symbol": "build_router",
            },
            {
                "id": "repo-entry",
                "path": "src/archbro/platform/persistence/postgres.py",
                "line_start": 86,
                "line_end": 87,
                "excerpt": "class PostgresProjectRepository:\n    \"\"\"PostgreSQL implementation of Jim's ProjectRepositoryPort.",
                "symbol": "PostgresProjectRepository",
            },
        ],
    }


def test_code_architecture_snapshot_is_deterministic_revision_pinned_implementation_evidence():
    request = CodeArchitectureSnapshotRequest.model_validate(snapshot_payload())

    first = build_code_architecture_snapshot("project-code", request)
    second = build_code_architecture_snapshot("project-code", request)

    assert first == second
    assert first["schema"] == CODE_ARCHITECTURE_SCHEMA
    assert first["classification"] == "IMPLEMENTATION_EVIDENCE"
    assert first["canonical_state_mutated"] is False
    assert first["repository"] == {
        "provider": "github",
        "slug": "Magic-Dala/archbro",
        "url": "https://github.com/Magic-Dala/archbro",
        "revision": REVISION,
        "revision_pinned": True,
    }
    assert first["evidence_verification"]["repository_checkout_verified"] is False

    nodes = {node["component_id"]: node for node in first["diagram"]["nodes"]}
    assert set(nodes) == {"web", "backend", "api", "repo"}
    assert nodes["backend"]["id"] == "code-node:backend"
    assert nodes["api"]["parent_id"] == "code-node:backend"
    assert nodes["backend"]["child_count"] == 2
    assert nodes["api"]["sources"][0]["href"].startswith(
        f"https://github.com/Magic-Dala/archbro/blob/{REVISION}/"
    )
    assert nodes["api"]["sources"][0]["href"].endswith("#L10-L11")

    assert all(edge["id"].startswith("code-edge:") for edge in first["diagram"]["edges"])
    assert {node["id"] for node in first["diagram"]["nodes"]} == {
        node["node_id"] for node in first["positioned_graph"]["nodes"]
    }
    assert {edge["id"] for edge in first["diagram"]["edges"]} == {
        edge["edge_id"] for edge in first["positioned_graph"]["edges"]
    }
    assert first["positioned_graph"]["architecture_version"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "abc123"),
        ("repository", "https://example.com/not-github/repo"),
    ],
)
def test_code_architecture_rejects_unpinned_or_non_github_repository(field: str, value: str):
    payload = snapshot_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        CodeArchitectureSnapshotRequest.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        "../secret.py",
        "/etc/passwd",
        "C:/Windows/system32.txt",
        "C:\\Windows\\system32.txt",
        ".git/config",
        "src/.git/config",
    ],
)
def test_code_architecture_rejects_non_repository_source_paths(path: str):
    payload = snapshot_payload()
    payload["source_evidence"][0]["path"] = path
    with pytest.raises(ValidationError):
        CodeArchitectureSnapshotRequest.model_validate(payload)


def test_code_architecture_rejects_mismatched_line_excerpt():
    payload = snapshot_payload()
    payload["source_evidence"][0]["line_end"] = 3
    with pytest.raises(ValidationError, match="line count"):
        CodeArchitectureSnapshotRequest.model_validate(payload)


def test_code_architecture_evidence_href_encodes_each_git_path_segment():
    payload = snapshot_payload()
    payload["source_evidence"][0]["path"] = "frontend/weird #name%?.js"
    request = CodeArchitectureSnapshotRequest.model_validate(payload)
    snapshot = build_code_architecture_snapshot("project-code", request)
    web = next(node for node in snapshot["diagram"]["nodes"] if node["component_id"] == "web")
    href = web["sources"][0]["href"]
    assert "/frontend/weird%20%23name%25%3F.js#L1-L2" in href
    assert "weird #name%?.js" not in href


def test_code_architecture_rejects_unknown_evidence_and_dangling_relationships():
    payload = snapshot_payload()
    payload["components"][0]["source_evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown source evidence"):
        CodeArchitectureSnapshotRequest.model_validate(payload)

    payload = snapshot_payload()
    payload["relationships"][0]["target"] = "missing"
    with pytest.raises(ValidationError, match="existing component ids"):
        CodeArchitectureSnapshotRequest.model_validate(payload)


@requires_database
def test_code_architecture_api_does_not_mutate_living_architecture(dsn):
    repository = PostgresProjectRepository(dsn)
    client = TestClient(build_app(repository, FakeModelProvider()))
    project = client.post(
        "/projects",
        json={"name": "Code Evidence", "goal": "Compare implementation with accepted architecture."},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]
    bootstrap = client.post(
        f"/projects/{project_id}/interactive-initial-architecture",
        json={
            "architecture": {
                "version": 1,
                "summary": "Accepted intent",
                "components": [
                    {
                        "id": "product",
                        "name": "Product",
                        "type": "system",
                        "responsibility": "Own product intent.",
                        "children": [
                            {
                                "id": "workspace",
                                "name": "Workspace",
                                "type": "ui",
                                "responsibility": "Own human interaction.",
                            }
                        ],
                    }
                ],
                "relationships": [],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [{"title": "Build workspace", "related_component": "workspace"}],
            "planning_trace": {
                "system_map_root_ids": ["product"],
                "scope_evaluations": [
                    {"scope_component_id": "product", "decomposition": "EXPANDED", "child_ids": ["workspace"]},
                    {"scope_component_id": "workspace", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Workspace owns one human interaction boundary with no independent architecture subsystem below it."},
                ],
                "reconciled": True,
            },
            "reasoning": "Fixture with recursive scope evaluation",
        },
    )
    assert bootstrap.status_code == 200

    before = client.get(f"/projects/{project_id}/architecture")
    assert before.status_code == 200

    response = client.post(
        f"/projects/{project_id}/code-architecture/snapshot",
        json=snapshot_payload(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"] == "IMPLEMENTATION_EVIDENCE"
    assert body["canonical_state_mutated"] is False

    after = client.get(f"/projects/{project_id}/architecture")
    assert after.status_code == 200
    assert after.json() == before.json()


@requires_database
def test_code_architecture_publish_is_durable_idempotent_and_latest_rebuilds_without_mutating_living_architecture(dsn):
    repository = PostgresProjectRepository(dsn)
    client = TestClient(build_app(repository, FakeModelProvider()))
    project = client.post(
        "/projects",
        json={"name": "Durable Code Evidence", "goal": "Keep implementation evidence separate from accepted intent."},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]
    bootstrap = client.post(
        f"/projects/{project_id}/interactive-initial-architecture",
        json={
            "architecture": {
                "version": 1,
                "summary": "Accepted living intent",
                "components": [
                    {
                        "id": "product",
                        "name": "Product",
                        "type": "system",
                        "responsibility": "Own accepted product intent.",
                        "children": [{"id": "product-core", "name": "Product Core", "type": "application", "responsibility": "Own the accepted product implementation boundary."}],
                    }
                ],
                "relationships": [],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [{"title": "Keep code evidence reviewable", "related_component": "product-core"}],
            "planning_trace": {
                "system_map_root_ids": ["product"],
                "scope_evaluations": [
                    {"scope_component_id": "product", "decomposition": "EXPANDED", "child_ids": ["product-core"]},
                    {"scope_component_id": "product-core", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Product Core is one implementation boundary with no independently addressable architecture subsystem below it."},
                ],
                "reconciled": True,
            },
            "reasoning": "Fixture with recursive scope evaluation",
        },
    )
    assert bootstrap.status_code == 200
    before = client.get(f"/projects/{project_id}/architecture").json()

    empty_latest = client.get(f"/projects/{project_id}/code-architecture/latest")
    assert empty_latest.status_code == 204
    assert empty_latest.content == b""

    first = client.post(f"/projects/{project_id}/code-architecture/snapshots", json=snapshot_payload())
    assert first.status_code == 200, first.text
    assert first.json()["derived_artifact_persisted"] is True
    assert first.json()["canonical_state_mutated"] is False
    event_id = first.json()["event_id"]

    second = client.post(f"/projects/{project_id}/code-architecture/snapshots", json=snapshot_payload())
    assert second.status_code == 200, second.text
    assert second.json()["event_id"] == event_id

    latest = client.get(f"/projects/{project_id}/code-architecture/latest")
    assert latest.status_code == 200, latest.text
    latest_body = latest.json()
    assert latest_body["event_id"] == event_id
    assert latest_body["repository"]["revision"] == REVISION
    assert latest_body["classification"] == "IMPLEMENTATION_EVIDENCE"
    assert latest_body["canonical_state_mutated"] is False
    assert {node["id"] for node in latest_body["diagram"]["nodes"]} == {
        "code-node:web",
        "code-node:backend",
        "code-node:api",
        "code-node:repo",
    }
    assert client.get(f"/projects/{project_id}/architecture").json() == before
