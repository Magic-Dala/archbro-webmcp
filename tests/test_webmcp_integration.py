from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.repository import ProjectRepository
from archbro.platform.runtime.app import build_app


def make_client() -> TestClient:
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "webmcp.db"))
    return TestClient(build_app(repo, FakeModelProvider()))


def test_webmcp_asset_uses_current_imperative_document_model_context_surface():
    client = make_client()

    index = client.get("/")
    assert index.status_code == 200
    assert 'src="/runtime-config.js"' in index.text
    assert 'type="module" src="/static/app.js?v=20260830-4"' in index.text
    assert 'type="module" src="/static/archbro-webmcp.js?v=competition-20260828-authz-atomic"' in index.text

    module = client.get("/static/archbro-webmcp.js")
    assert module.status_code == 200
    assert "document.modelContext.registerTool()" in module.text
    app = client.get("/static/app.js")
    assert app.status_code == 200
    assert "WEBMCP_AGENT_MODE" in app.text
    assert "Built-in architecture generation is disabled in WebMCP Agent Mode." in app.text
    brief_block = app.text.split("async getProjectBrief()", 1)[1].split("async getDecisionContext()", 1)[0]
    assert "await refresh();" in brief_block
    assert "appInitializationPromise = initialize();" in app.text
    for method in (
        "bootstrapProject",
        "getProjectBrief",
        "getDecisionContext",
        "submitAgentRecommendation",
        "updateTaskStatus",
        "focusPendingReview",
    ):
        method_block = app.text.split(f"async {method}", 1)[1].split("\n  async ", 1)[0]
        assert "await ensureAppInitialized();" in method_block
    assert "navigator.modelContext" not in module.text
    assert "provideContext" not in module.text

    expected = [
        "ping",
        "bootstrap_project",
        "get_project_brief",
        "get_decision_context",
        "submit_agent_recommendation",
        "update_task_status",
        "focus_pending_review",
    ]
    for tool_name in expected:
        assert tool_name in module.text

    for removed_low_level_tool in [
        "create_project",
        "submit_initial_architecture",
        "get_current_project_context",
        "inspect_project_status",
        "get_recent_activity",
        "report_project_change",
        "focus_workspace_item",
    ]:
        assert removed_low_level_tool not in module.text


def test_webmcp_bootstrap_bridge_is_single_agent_call_without_builtin_model():
    client = make_client()
    app = client.get("/static/app.js")
    assert app.status_code == 200

    assert "async bootstrapProject({name, goal, architectureSummary, components = [], relationships = [], tasks = [], reasoning}" in app.text
    bootstrap_block = app.text.split("async bootstrapProject(", 1)[1].split("async createProject", 1)[0]
    assert "generateInitialArchitecture()" not in bootstrap_block
    assert "POST" in bootstrap_block
    assert "interactive-initial-architecture" in bootstrap_block
    assert "built_in_model_called: false" in bootstrap_block

    human_ui_block = app.text.split("async function confirmGoalAndGenerate()", 1)[1].split("function backToCurrentProject()", 1)[0]
    assert "await generateInitialArchitecture()" in human_ui_block


def test_host_agent_can_supply_initial_architecture_without_model_provider_call():
    client = make_client()

    project = client.post("/projects", json={
        "name": "Codex Host Demo",
        "goal": "Build a React frontend, FastAPI backend, and PostgreSQL persistence.",
        "description": "",
    })
    assert project.status_code == 200
    project_id = project.json()["id"]

    bootstrap = client.post(f"/projects/{project_id}/interactive-initial-architecture", json={
        "architecture": {
            "version": 1,
            "summary": "React frontend calls FastAPI, backed by PostgreSQL.",
            "components": [
                {"id": "frontend", "name": "React frontend", "type": "frontend", "responsibility": "Project collaboration UI"},
                {"id": "backend", "name": "FastAPI backend", "type": "backend", "responsibility": "REST API and orchestration"},
                {"id": "database", "name": "PostgreSQL", "type": "database", "responsibility": "Persist project state"},
            ],
            "relationships": [
                {"source": "frontend", "target": "backend", "relationship_type": "REST", "description": "Frontend invokes backend"},
                {"source": "backend", "target": "database", "relationship_type": "PERSISTENCE", "description": "Backend persists state"},
            ],
            "decisions": ["Use React", "Use FastAPI", "Use PostgreSQL"],
            "assumptions": [],
            "risks": [],
        },
        "tasks": [
            {"title": "Build FastAPI backend skeleton", "related_component": "backend", "acceptance_criteria": ["API starts"]},
            {"title": "Build React frontend shell", "related_component": "frontend", "acceptance_criteria": ["UI renders"]},
            {"title": "Prepare PostgreSQL persistence", "related_component": "database", "acceptance_criteria": ["Persistence works"]},
        ],
        "reasoning": "The host agent derived Architecture v1 directly from the stored goal.",
    })
    assert bootstrap.status_code == 200
    body = bootstrap.json()
    assert body["provider"] == "webmcp-agent"
    assert body["model"] == "external-interactive"
    assert body["architecture"]["version"] == 1
    assert len(body["tasks"]) == 3

    activity = client.get(f"/projects/{project_id}/events?limit=10").json()
    assert activity[-1]["payload"]["external_source"] == "WEBMCP_AGENT"
    assert activity[-1]["payload"]["intent"] == "INITIAL_ARCHITECTURE"


def test_webmcp_agent_can_create_reviewable_recommendation_without_model_provider():
    client = make_client()
    project = client.post("/projects", json={
        "name": "Interactive Decision Demo",
        "goal": "Build a React frontend, FastAPI backend, and PostgreSQL persistence.",
        "description": "",
    })
    project_id = project.json()["id"]
    bootstrap = client.post(f"/projects/{project_id}/interactive-initial-architecture", json={
        "architecture": {
            "version": 1,
            "summary": "React frontend calls FastAPI, backed by PostgreSQL.",
            "components": [
                {"id": "frontend", "name": "React frontend", "type": "frontend", "responsibility": "UI"},
                {"id": "backend", "name": "FastAPI backend", "type": "backend", "responsibility": "API"},
                {"id": "database", "name": "PostgreSQL", "type": "database", "responsibility": "Persistence"},
            ],
            "relationships": [],
            "decisions": [],
            "assumptions": [],
            "risks": [],
        },
        "tasks": [{"title": "Prepare persistence", "related_component": "database"}],
        "reasoning": "Host-generated initial architecture.",
    })
    assert bootstrap.status_code == 200

    recommendation = client.post(f"/projects/{project_id}/agent-recommendations", json={
        "recommendation": "ACCEPT_PROPOSED_CHANGE",
        "reasoning": "Monitoring shows PostgreSQL is unavailable, so the accepted persistence boundary needs review.",
        "evidence": ["PostgreSQL staging health checks are failing."],
        "observed_change": "The selected persistence technology is not viable in staging.",
        "affected_components": ["database", "backend"],
        "proposed_changes": [{
            "operation": "replace_component",
            "component_id": "database",
            "new_name": "Firestore",
            "new_type": "database",
            "new_responsibility": "Persist project state",
        }],
        "impact": "Backend persistence work and database tasks must be re-evaluated.",
    })
    assert recommendation.status_code == 200
    body = recommendation.json()
    assert body["provider"] == "webmcp-agent"
    assert body["model"] == "external-interactive"
    assert body["architecture_review_required"] is True
    assert body["proposal"]["status"] == "PENDING"

    architecture = client.get(f"/projects/{project_id}/architecture").json()
    assert architecture["version"] == 1
    assert any(component["name"] == "PostgreSQL" for component in architecture["components"])
