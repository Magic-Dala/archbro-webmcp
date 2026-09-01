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
                    {"id": "react-web-client", "name": "React Web Client", "type": "frontend system", "responsibility": "Own collaborative issue experience", "children": [{"id": "issue-workspace", "name": "Issue Workspace", "type": "ui", "responsibility": "Render collaborative issue workflows"}, {"id": "client-state-adapter", "name": "Client State Adapter", "type": "client integration", "responsibility": "Own client state and synchronization"}]},
                    {"id": "fastapi-application", "name": "FastAPI Application", "type": "backend system", "responsibility": "Own API and privileged workflows", "children": [{"id": "api-layer", "name": "API Layer", "type": "service", "responsibility": "Receive application requests"}, {"id": "application-services", "name": "Application Services", "type": "service", "responsibility": "Run issue workflows"}]},
                    {"id": "postgresql-database", "name": "Persistence Platform", "type": "data system", "responsibility": "Own durable issue state", "children": [{"id": "project-state-store", "name": "PostgreSQL Project State", "type": "database", "responsibility": "Persist project and issue state"}]},
                    {"id": "realtime-collaboration-channel", "name": "Realtime Collaboration", "type": "realtime system", "responsibility": "Own custom realtime collaboration", "children": [{"id": "websocket-channel", "name": "WebSocket Channel", "type": "realtime transport", "responsibility": "Deliver collaboration events"}]},
                ],
                "relationships": [
                    {"source": "issue-workspace", "target": "client-state-adapter", "relationship_type": "STATE_ACTION"},
                    {"source": "client-state-adapter", "target": "api-layer", "relationship_type": "HTTPS JSON REST"},
                    {"source": "api-layer", "target": "application-services", "relationship_type": "APPLICATION_CALL"},
                    {"source": "application-services", "target": "project-state-store", "relationship_type": "SQL"},
                    {"source": "client-state-adapter", "target": "websocket-channel", "relationship_type": "WebSocket"},
                ],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [
                {"title": "Build collaborative React interface", "related_component": "issue-workspace"},
                {"title": "Define PostgreSQL schema and migrations", "related_component": "project-state-store"},
                {"title": "Add custom realtime updates", "related_component": "websocket-channel"},
            ],
            "planning_trace": {
                "system_map_root_ids": ["react-web-client", "fastapi-application", "postgresql-database", "realtime-collaboration-channel"],
                "scope_evaluations": [
                    {"scope_component_id": "react-web-client", "decomposition": "EXPANDED", "child_ids": ["issue-workspace", "client-state-adapter"]},
                    {"scope_component_id": "issue-workspace", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Issue Workspace is one collaboration interaction boundary with no independent architecture subsystem below it."},
                    {"scope_component_id": "client-state-adapter", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Client State Adapter is one synchronization boundary with no independently addressable architecture subsystem below it."},
                    {"scope_component_id": "fastapi-application", "decomposition": "EXPANDED", "child_ids": ["api-layer", "application-services"]},
                    {"scope_component_id": "api-layer", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "API Layer is one HTTP request boundary with no independently addressable architecture subsystem below it."},
                    {"scope_component_id": "application-services", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Application Services is one issue-workflow boundary with no independent architecture subsystem below it."},
                    {"scope_component_id": "postgresql-database", "decomposition": "EXPANDED", "child_ids": ["project-state-store"]},
                    {"scope_component_id": "project-state-store", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Project State Store is one durable persistence boundary and requires no lower architecture split."},
                    {"scope_component_id": "realtime-collaboration-channel", "decomposition": "EXPANDED", "child_ids": ["websocket-channel"]},
                    {"scope_component_id": "websocket-channel", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "WebSocket Channel is one realtime transport boundary and requires no lower architecture split."},
                ],
                "reconciled": True,
            },
            "reasoning": "The host agent recursively evaluated the initial plan from the user goal.",
        },
    )
    assert bootstrap.status_code == 200
    assert bootstrap.json()["architecture"]["version"] == 1

    # An operational incident alone is not sufficient reason to rewrite accepted architecture.
    keep = client.post(
        f"/projects/{project_id}/agent-recommendations",
        json={
            "recommendation": "KEEP_CURRENT",
            "expected_architecture_version": 1,
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
            "expected_architecture_version": 1,
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
