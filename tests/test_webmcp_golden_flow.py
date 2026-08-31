from fastapi.testclient import TestClient

from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database

pytestmark = requires_database


def make_client(dsn) -> TestClient:
    repo = PostgresProjectRepository(dsn)
    return TestClient(build_app(repo, FakeModelProvider()))


def test_webmcp_golden_governance_and_execution_loop(dsn):
    client = make_client(dsn)

    project = client.post(
        "/projects",
        json={
            "name": "WebMCP Golden Demo",
            "goal": "Build a collaborative issue tracker with React, FastAPI, PostgreSQL, and realtime collaboration.",
            "description": "",
        },
    ).json()
    project_id = project["id"]

    bootstrap = client.post(
        f"/projects/{project_id}/interactive-initial-architecture",
        json={
            "architecture": {
                "version": 1,
                "summary": "React uses FastAPI, PostgreSQL, and a custom realtime channel.",
                "components": [
                    {"id": "react-web-client", "name": "React Web Client", "type": "frontend", "responsibility": "Collaborative issue UI"},
                    {"id": "fastapi-application", "name": "FastAPI Application", "type": "backend", "responsibility": "Application API"},
                    {"id": "postgresql-database", "name": "PostgreSQL", "type": "database", "responsibility": "Durable persistence"},
                    {"id": "realtime-collaboration-channel", "name": "Realtime Collaboration Channel", "type": "realtime", "responsibility": "WebSocket collaboration"},
                ],
                "relationships": [
                    {"source": "react-web-client", "target": "fastapi-application", "relationship_type": "HTTPS JSON REST"},
                    {"source": "fastapi-application", "target": "postgresql-database", "relationship_type": "SQL"},
                    {"source": "react-web-client", "target": "realtime-collaboration-channel", "relationship_type": "WebSocket"},
                ],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [
                {"title": "Build collaborative React interface", "related_component": "react-web-client"},
                {"title": "Define PostgreSQL schema and migrations", "related_component": "postgresql-database"},
                {"title": "Add custom realtime updates", "related_component": "realtime-collaboration-channel"},
            ],
            "reasoning": "The host agent generated the initial plan from the user goal.",
        },
    )
    assert bootstrap.status_code == 200
    assert bootstrap.json()["architecture"]["version"] == 1

    # An operational incident alone is not sufficient reason to rewrite accepted architecture.
    keep = client.post(
        f"/projects/{project_id}/agent-recommendations",
        json={
            "recommendation": "KEEP_CURRENT",
            "reasoning": "The PostgreSQL connection-pool health check is an operational incident and does not invalidate the accepted persistence boundary.",
            "evidence": ["PostgreSQL staging connection pool is failing health checks."],
            "observed_change": "Staging connectivity is degraded.",
            "affected_components": ["postgresql-database"],
            "proposed_changes": [],
            "impact": "",
        },
    )
    assert keep.status_code == 200
    assert keep.json()["architecture_review_required"] is False
    assert keep.json()["proposal"] is None
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 1

    # A later approved release constraint crosses architecture boundaries and is reviewable.
    recommendation = client.post(
        f"/projects/{project_id}/agent-recommendations",
        json={
            "recommendation": "ACCEPT_PROPOSED_CHANGE",
            "reasoning": "The approved release now requires offline-first clients and managed Firebase persistence, so PostgreSQL plus a custom WebSocket channel no longer satisfies the accepted requirements.",
            "evidence": [
                "Approved release requirement: offline-first clients with automatic background synchronization.",
                "Platform standard: Firebase Auth and Cloud Firestore; no custom realtime persistence channel.",
            ],
            "observed_change": "Persistence and realtime responsibilities moved to Firebase-managed client synchronization.",
            "affected_components": [
                "postgresql-database",
                "realtime-collaboration-channel",
                "react-web-client",
                "fastapi-application",
            ],
            "proposed_changes": [
                {
                    "operation": "replace_component",
                    "component_id": "postgresql-database",
                    "replacement": {
                        "id": "firebase-managed-data-platform",
                        "name": "Firebase Managed Data Platform",
                        "type": "Cloud Firestore and Firebase Auth",
                        "responsibility": "Managed identity, persistence, offline sync, and realtime state delivery.",
                    },
                },
                {"operation": "remove_component", "component_id": "realtime-collaboration-channel"},
                {
                    "operation": "update_component",
                    "component_id": "react-web-client",
                    "changes": {"responsibility": "Use Firebase SDK for offline persistence, background sync, and snapshot listeners."},
                },
                {
                    "operation": "update_component",
                    "component_id": "fastapi-application",
                    "changes": {"responsibility": "Retain privileged operations and integrations through Firebase Admin SDK."},
                },
                {
                    "operation": "replace_relationships",
                    "changes": [
                        {"source": "react-web-client", "target": "firebase-managed-data-platform", "type": "Firebase SDK"},
                        {"source": "react-web-client", "target": "fastapi-application", "type": "HTTPS JSON REST"},
                        {"source": "fastapi-application", "target": "firebase-managed-data-platform", "type": "Firebase Admin SDK"},
                    ],
                },
            ],
            "impact": "Persistence, realtime synchronization, client state handling, and privileged backend access change.",
        },
    )
    assert recommendation.status_code == 200
    proposal = recommendation.json()["proposal"]
    assert proposal["status"] == "PENDING"
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 1

    # The host agent cannot approve. Explicit human acceptance performs the version transition.
    accepted = client.post(
        f"/projects/{project_id}/architecture/proposals/{proposal['id']}/accept"
    )
    assert accepted.status_code == 200
    architecture = client.get(f"/projects/{project_id}/architecture").json()
    assert architecture["version"] == 2
    component_ids = {component["id"] for component in architecture["components"]}
    assert "firebase-managed-data-platform" in component_ids
    assert "postgresql-database" not in component_ids
    assert "realtime-collaboration-channel" not in component_ids

    tasks = client.get(f"/projects/{project_id}/tasks").json()
    ready = [task for task in tasks if task["status"] == "TODO"]
    replacement_task = next(task for task in ready if task["related_component"] == "firebase-managed-data-platform")
    assert "Firebase Managed Data Platform" in replacement_task["title"]
    removed_task = next(task for task in tasks if task["title"] == "Add custom realtime updates")
    assert removed_task["status"] == "BLOCKED"
    assert removed_task["related_component"] is None

    # The accepted architecture yields executable work and the host can continue through the deterministic task boundary.
    transition = client.post(
        f"/projects/{project_id}/events",
        json={
            "type": "TASK_UPDATED",
            "source": "HUMAN",
            "payload": {
                "task_id": replacement_task["id"],
                "status": "IN_PROGRESS",
                "message": "Host agent started the next ready task after human architecture approval.",
            },
        },
    )
    assert transition.status_code == 200
    assert transition.json()["provider"] == "deterministic"
    tasks = client.get(f"/projects/{project_id}/tasks").json()
    started = next(task for task in tasks if task["id"] == replacement_task["id"])
    assert started["status"] == "IN_PROGRESS"
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 2
