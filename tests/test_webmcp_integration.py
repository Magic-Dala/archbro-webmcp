import copy
from pathlib import Path
import re

from fastapi.testclient import TestClient

from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database

pytestmark = requires_database


def make_client(dsn) -> TestClient:
    repo = PostgresProjectRepository(dsn)
    return TestClient(build_app(repo, FakeModelProvider()))


def bootstrap_semantic_project(client: TestClient) -> str:
    project = client.post("/projects", json={
        "name": "Semantic WebMCP",
        "goal": "Ship a web workspace backed by an API.",
        "description": "",
    })
    assert project.status_code == 200
    project_id = project.json()["id"]
    bootstrap = client.post(f"/projects/{project_id}/interactive-initial-architecture", json={
        "architecture": {
            "version": 1,
            "summary": "Web calls API.",
            "components": [
                {"id": "web", "name": "Web Experience", "type": "frontend system", "responsibility": "Own workspace interaction", "children": [{"id": "web-ui", "name": "Workspace UI", "type": "ui", "responsibility": "Render workspace workflows"}]},
                {"id": "api", "name": "API Platform", "type": "backend system", "responsibility": "Own product API processing", "children": [{"id": "api-service", "name": "API Service", "type": "service", "responsibility": "Serve product requests"}]},
            ],
            "relationships": [
                {"source": "web-ui", "target": "api-service", "relationship_type": "HTTPS"},
            ],
            "decisions": [],
            "assumptions": [],
            "risks": [],
        },
        "tasks": [{"title": "Build API", "related_component": "api-service"}],
        "planning_trace": {
            "system_map_root_ids": ["web", "api"],
            "scope_evaluations": [
                {"scope_component_id": "web", "decomposition": "EXPANDED", "child_ids": ["web-ui"]},
                {"scope_component_id": "web-ui", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Workspace UI owns one user interaction boundary with no independent architecture subsystem below it."},
                {"scope_component_id": "api", "decomposition": "EXPANDED", "child_ids": ["api-service"]},
                {"scope_component_id": "api-service", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "API Service owns one request-processing boundary with no independently addressable subsystem below it."},
            ],
            "reconciled": True,
        },
        "reasoning": "Host supplied the recursively evaluated initial accepted plan.",
    })
    assert bootstrap.status_code == 200, bootstrap.text
    return project_id


def test_real_host_acceptance_invokes_discovered_ping_before_page_api_diagnostics():
    root = Path(__file__).resolve().parents[1]
    runbook = (root / "docs" / "CODEX_WEBMCP_ACCEPTANCE.md").read_text(encoding="utf-8")
    assert "https://archbro-dev.magicdala.com/?mode=webmcp" in runbook
    assert "must not use Playwright" in runbook
    assert "Do not inspect `document.modelContext` as a prerequisite" in runbook
    assert "Do not switch to another testing mechanism." in runbook


def test_webmcp_asset_uses_current_imperative_document_model_context_surface(dsn):
    client = make_client(dsn)

    index = client.get("/")
    assert index.status_code == 200
    assert 'src="/runtime-config.js"' in index.text
    assert 'src="/static/firebase-auth-client.js?v=20260901-auth-providers"' in index.text
    assert 'type="module" src="/static/app.js?v=20260901-auth-providers"' in index.text
    assert 'type="module" src="/static/archbro-webmcp.js?v=20260831-webmcp-build-watchdog"' in index.text
    assert index.headers["cache-control"] == "no-store, max-age=0"

    module = client.get("/static/archbro-webmcp.js")
    assert module.status_code == 200
    assert "document.modelContext.registerTool()" in module.text
    app = client.get("/static/app.js")
    assert app.status_code == 200
    assert "WEBMCP_AGENT_MODE" in app.text
    assert "WEBMCP_PUBLIC_HOSTS" in app.text
    assert "archbro-dev.magicdala.com" in app.text
    assert "archbro.magicdala.com" in app.text
    assert "Built-in architecture generation is disabled in WebMCP Agent Mode." in app.text
    brief_block = app.text.split("async getProjectBrief()", 1)[1].split("async getDecisionContext()", 1)[0]
    assert "await refresh();" in brief_block
    assert "appInitializationPromise = initialize();" in app.text
    for method in (
        "bootstrapProject",
        "expandArchitectureScope",
        "getDecisionContext",
        "submitAgentRecommendation",
        "createTask",
        "updateTaskStatus",
        "recordProjectObservation",
    ):
        method_block = app.text.split(f"async {method}", 1)[1].split("\n  async ", 1)[0]
        assert "await ensureAppInitialized();" in method_block
    assert "globalThis.navigator?.modelContext" in module.text
    assert "Object.defineProperty(globalThis.document, 'modelContext'" in module.text
    assert "provideContext" not in module.text

    expected = [
        "ping",
        "get_agent_context",
        "get_architecture_diagram",
        "get_architecture_node_context",
        "find_architecture_path",
        "bootstrap_project",
        "expand_architecture_scope",
        "get_architecture_decision_context",
        "submit_architecture_recommendation",
        "publish_code_architecture",
        "get_code_architecture",
        "create_task",
        "update_task_status",
        "record_project_observation",
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
        "focus_pending_review",
        "get_project_brief",
        "get_scoped_diagram",
        "get_node_context",
        "build_code_architecture_snapshot",
        "publish_code_architecture_snapshot",
        "get_latest_code_architecture",
        "get_decision_context",
        "submit_agent_recommendation",
    ]:
        assert removed_low_level_tool not in module.text


def test_webmcp_manifest_and_ping_have_server_build_identity(dsn, monkeypatch):
    client = make_client(dsn)
    monkeypatch.delenv("ARCHBRO_MCP_SERVERS_JSON", raising=False)

    manifest_response = client.get("/webmcp-manifest.json")
    assert manifest_response.status_code == 200
    assert manifest_response.headers["cache-control"] == "no-store, max-age=0"
    manifest = manifest_response.json()
    assert manifest["surface"] == "archbro-webmcp"
    assert manifest["surface_version"] == "archbro.semantic-webmcp.v4"
    assert manifest["expected_tool_count"] == 14
    assert manifest["connected_mcp_gateway_configured"] is False
    assert len(manifest["asset_sha256"]) == 64

    runtime_config = client.get("/runtime-config.js")
    assert runtime_config.headers["cache-control"] == "no-store, max-age=0"
    assert manifest["asset_sha256"] in runtime_config.text
    assert '"webmcp_expected_tool_count": 14' in runtime_config.text

    module = client.get("/static/archbro-webmcp.js")
    assert module.headers["cache-control"] == "no-store, max-age=0"
    assert "await verifyWebMcpRuntime" in module.text
    assert "expected_tool_count: runtime.manifest.expected_tool_count" in module.text
    assert "WEBMCP_RUNTIME_CHECK_INTERVAL_MS" in module.text


def test_webmcp_bootstrap_bridge_is_single_agent_call_without_builtin_model(dsn):
    client = make_client(dsn)
    app = client.get("/static/app.js")
    assert app.status_code == 200

    assert "async bootstrapProject({name, goal, architectureSummary, components = [], relationships = [], tasks = [], planningTrace, reasoning}" in app.text
    bootstrap_block = app.text.split("async bootstrapProject(", 1)[1].split("async createProject", 1)[0]
    assert "generateInitialArchitecture()" not in bootstrap_block
    assert "POST" in bootstrap_block
    assert "interactive-initial-architecture" in bootstrap_block
    assert "built_in_model_called: false" in bootstrap_block
    assert "normalizeWebMcpArchitectureComponents(components, {requireIds: true})" in bootstrap_block
    assert "normalizeInitialPlanningTrace(planningTrace, normalizedComponents)" in bootstrap_block
    assert "planning_trace: normalizedPlanningTrace" in bootstrap_block
    assert "children: []" not in bootstrap_block

    human_ui_block = app.text.split("async function confirmGoalAndGenerate()", 1)[1].split("function backToCurrentProject()", 1)[0]
    assert "await generateInitialArchitecture()" in human_ui_block


def test_webmcp_hierarchy_tools_expose_scoped_read_and_reviewable_additive_expansion(dsn):
    client = make_client(dsn)
    module = client.get("/static/archbro-webmcp.js")
    app = client.get("/static/app.js")
    assert module.status_code == 200
    assert app.status_code == 200

    expansion_block = app.text.split("async expandArchitectureScope(", 1)[1].split("async createProject", 1)[0]
    assert "operation: 'expand_scope'" in expansion_block
    assert "submitAgentRecommendation" in expansion_block
    assert "ACCEPT_PROPOSED_CHANGE" in expansion_block
    assert "/architecture/proposals/" not in expansion_block


def test_webmcp_architecture_focus_navigates_to_the_requested_nodes_parent_scope(dsn):
    client = make_client(dsn)
    app = client.get("/static/app.js")
    assert app.status_code == 200
    focus_block = app.text.split("async focusItem({kind, id = null} = {})", 1)[1].split("async reportChange", 1)[0]
    assert "findArchitectureParentId(node.id)" in focus_block
    assert "navigateGraphScope(parentScopeComponentId ?? null, {focusComponentId: node.id})" in focus_block
    assert "navigateGraphScope(null, {focusComponentId: node.id})" not in focus_block


def test_semantic_task_routes_commit_mutation_and_audit_event_through_one_repository_transition(dsn):
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "archbro" / "backend" / "api" / "agent_surface.py").read_text(encoding="utf-8")
    create_block = source.split('@router.post("/projects/{project_id}/tasks")', 1)[1].split('@router.patch("/projects/{project_id}/tasks/{task_id}/status")', 1)[0]
    update_block = source.split('@router.patch("/projects/{project_id}/tasks/{task_id}/status")', 1)[1].split('@router.post("/projects/{project_id}/observations")', 1)[0]
    for block in (create_block, update_block):
        assert "repository.commit_event_actions(" in block
        assert "expected_task_updated_at=plan.expected_task_updated_at" in block
        assert "repository.save_event(" not in block
        assert "executor.apply_plan(" not in block
        assert "executor.apply(" not in block
    assert "except ConcurrentStateError as exc:" in update_block
    assert "HTTPException(status_code=409" in update_block

    module = (root / "frontend" / "web" / "archbro-webmcp.js").read_text(encoding="utf-8")
    create_tool = module.split("name: `${TOOL_PREFIX}create_task`", 1)[1].split("name: `${TOOL_PREFIX}update_task_status`", 1)[0]
    assert "request_id" in create_tool
    assert "required: ['request_id', 'title']" in create_tool


def test_semantic_task_and_observation_apis_are_deterministic_without_model_runs(dsn):
    client = make_client(dsn)
    project_id = bootstrap_semantic_project(client)
    architecture_before = client.get(f"/projects/{project_id}/architecture").json()
    initial_task_count = len(client.get(f"/projects/{project_id}/tasks").json())
    assert client.get(f"/projects/{project_id}/agent-runs").json() == []

    created = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "semantic-api-contract-task",
        "title": "Add API contract tests",
        "description": "Cover the accepted API boundary.",
        "owner": "AGENT",
        "related_component": "api",
        "acceptance_criteria": ["Contract tests pass"],
    })
    assert created.status_code == 200, created.text
    created_body = created.json()
    assert created_body["built_in_model_called"] is False
    assert created_body["idempotent_replay"] is False
    task_id = created_body["task"]["id"]
    assert created_body["task"]["status"] == "TODO"
    assert created_body["task"]["source"] == "AGENT"

    retried = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "semantic-api-contract-task",
        "title": "Add API contract tests",
        "description": "Cover the accepted API boundary.",
        "owner": "AGENT",
        "related_component": "api",
        "acceptance_criteria": ["Contract tests pass"],
    })
    assert retried.status_code == 200, retried.text
    assert retried.json()["task"]["id"] == task_id
    assert retried.json()["event_id"] == created_body["event_id"]
    assert retried.json()["idempotent_replay"] is True
    assert len(client.get(f"/projects/{project_id}/tasks").json()) == initial_task_count + 1

    conflicting_retry = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "semantic-api-contract-task",
        "title": "Different logical task",
        "related_component": "api",
    })
    assert conflicting_retry.status_code == 409, conflicting_retry.text
    assert len(client.get(f"/projects/{project_id}/tasks").json()) == initial_task_count + 1

    started = client.patch(
        f"/projects/{project_id}/tasks/{task_id}/status",
        json={"status": "IN_PROGRESS"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["task"]["status"] == "IN_PROGRESS"
    assert started.json()["built_in_model_called"] is False

    retry_after_transition = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "semantic-api-contract-task",
        "title": "Add API contract tests",
        "description": "Cover the accepted API boundary.",
        "owner": "AGENT",
        "related_component": "api",
        "acceptance_criteria": ["Contract tests pass"],
    })
    assert retry_after_transition.status_code == 200, retry_after_transition.text
    assert retry_after_transition.json()["task"]["id"] == task_id
    assert retry_after_transition.json()["task"]["status"] == "IN_PROGRESS"
    assert retry_after_transition.json()["event_id"] == created_body["event_id"]
    assert retry_after_transition.json()["idempotent_replay"] is True
    assert len(client.get(f"/projects/{project_id}/tasks").json()) == initial_task_count + 1

    completed = client.patch(
        f"/projects/{project_id}/tasks/{task_id}/status",
        json={"status": "DONE"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["task"]["status"] == "DONE"
    assert completed.json()["built_in_model_called"] is False

    observation = client.post(f"/projects/{project_id}/observations", json={
        "summary": "API contract tests now pass at the accepted boundary.",
        "evidence": ["CI contract suite passed."],
        "related_components": ["api"],
        "related_task_id": task_id,
    })
    assert observation.status_code == 200, observation.text
    observation_body = observation.json()
    assert observation_body["built_in_model_called"] is False
    assert observation_body["canonical_architecture_mutated"] is False
    assert observation_body["event"]["payload"]["intent"] == "PROJECT_OBSERVATION"

    assert client.get(f"/projects/{project_id}/architecture").json() == architecture_before
    assert client.get(f"/projects/{project_id}/agent-runs").json() == []


def test_semantic_task_and_observation_apis_reject_invalid_project_references(dsn):
    client = make_client(dsn)
    project_id = bootstrap_semantic_project(client)
    existing_task = client.get(f"/projects/{project_id}/tasks").json()[0]

    bad_component = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "bad-component-task",
        "title": "Bad component task",
        "related_component": "does-not-exist",
    })
    assert bad_component.status_code == 422

    bad_dependency = client.post(f"/projects/{project_id}/tasks", json={
        "request_id": "bad-dependency-task",
        "title": "Bad dependency task",
        "related_component": "api",
        "dependencies": ["task-not-in-project"],
    })
    assert bad_dependency.status_code == 422

    invalid_transition = client.patch(
        f"/projects/{project_id}/tasks/{existing_task['id']}/status",
        json={"status": "DONE"},
    )
    assert invalid_transition.status_code == 409

    bad_observation = client.post(f"/projects/{project_id}/observations", json={
        "summary": "Evidence refers to an unknown component.",
        "evidence": ["External source result"],
        "related_components": ["does-not-exist"],
    })
    assert bad_observation.status_code == 422


def test_runtime_config_exposes_connected_mcp_capability_only_when_configured(dsn, monkeypatch):
    client = make_client(dsn)

    monkeypatch.delenv("ARCHBRO_MCP_SERVERS_JSON", raising=False)
    empty_config = client.get("/runtime-config.js").text
    assert '"connected_mcp_gateway_configured": false' in empty_config

    monkeypatch.setenv("ARCHBRO_MCP_SERVERS_JSON", "[]")
    empty_list_config = client.get("/runtime-config.js").text
    assert '"connected_mcp_gateway_configured": false' in empty_list_config

    monkeypatch.setenv(
        "ARCHBRO_MCP_SERVERS_JSON",
        '[{"id":"github","name":"GitHub","url":"https://example.com/mcp","allow_tools":["get_*"]}]',
    )
    configured = client.get("/runtime-config.js").text
    assert '"connected_mcp_gateway_configured": true' in configured

    monkeypatch.setenv("ARCHBRO_MCP_SERVERS_JSON", "not-json")
    invalid = client.get("/runtime-config.js").text
    assert '"connected_mcp_gateway_configured": false' in invalid


def test_host_agent_can_supply_initial_architecture_without_model_provider_call(dsn):
    client = make_client(dsn)

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
                {"id": "frontend", "name": "Frontend Experience", "type": "frontend system", "responsibility": "Own project collaboration UI", "children": [{"id": "workspace", "name": "Workspace", "type": "ui", "responsibility": "Render project workflows"}]},
                {"id": "backend", "name": "Backend Platform", "type": "backend system", "responsibility": "Own API and application workflows", "children": [{"id": "api", "name": "FastAPI API", "type": "service", "responsibility": "Serve project requests"}]},
                {"id": "data", "name": "Data Platform", "type": "data system", "responsibility": "Own durable project state", "children": [{"id": "postgresql", "name": "PostgreSQL", "type": "database", "responsibility": "Persist project state"}]},
            ],
            "relationships": [
                {"source": "workspace", "target": "api", "relationship_type": "REST", "description": "Workspace invokes backend API"},
                {"source": "api", "target": "postgresql", "relationship_type": "PERSISTENCE", "description": "API persists state"},
            ],
            "decisions": ["Use React", "Use FastAPI", "Use PostgreSQL"],
            "assumptions": [],
            "risks": [],
        },
        "tasks": [
            {"title": "Build FastAPI backend skeleton", "related_component": "api", "acceptance_criteria": ["API starts"]},
            {"title": "Build React frontend shell", "related_component": "workspace", "acceptance_criteria": ["UI renders"]},
            {"title": "Prepare PostgreSQL persistence", "related_component": "postgresql", "acceptance_criteria": ["Persistence works"]},
        ],
        "planning_trace": {
            "system_map_root_ids": ["frontend", "backend", "data"],
            "scope_evaluations": [
                {"scope_component_id": "frontend", "decomposition": "EXPANDED", "child_ids": ["workspace"]},
                {"scope_component_id": "workspace", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Workspace owns one user interaction boundary with no independent architecture subsystem below it."},
                {"scope_component_id": "backend", "decomposition": "EXPANDED", "child_ids": ["api"]},
                {"scope_component_id": "api", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "FastAPI API owns one request boundary with no independently addressable subsystem below it."},
                {"scope_component_id": "data", "decomposition": "EXPANDED", "child_ids": ["postgresql"]},
                {"scope_component_id": "postgresql", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "PostgreSQL is the durable persistence implementation boundary and needs no lower architecture split."},
            ],
            "reconciled": True,
        },
        "reasoning": "The host agent derived Architecture v1 recursively from the stored goal.",
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
    assert activity[-1]["payload"]["planning_trace"]["reconciled"] is True


def test_external_initial_architecture_rejects_missing_planning_trace(dsn):
    client = make_client(dsn)
    project = client.post("/projects", json={"name": "No trace", "goal": "Build a small web system.", "description": ""})
    assert project.status_code == 200
    project_id = project.json()["id"]
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json={
        "architecture": {
            "version": 1,
            "summary": "Attempted untraced architecture",
            "components": [{"id": "backend", "name": "Backend", "type": "system", "responsibility": "Own backend work", "children": [{"id": "api", "name": "API", "type": "service", "responsibility": "Serve requests"}]}],
            "relationships": [], "decisions": [], "assumptions": [], "risks": [],
        },
        "tasks": [{"title": "Build API", "related_component": "api"}],
        "reasoning": "This intentionally omits the required recursive planning trace.",
    })
    assert rejected.status_code == 422
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 0


def test_external_initial_planning_trace_requires_recursive_scope_evaluation(dsn):
    client = make_client(dsn)
    project = client.post("/projects", json={
        "name": "Outside-in trace",
        "goal": "Validate recursive external architecture planning.",
        "description": "",
    })
    assert project.status_code == 200
    project_id = project.json()["id"]
    payload = {
        "architecture": {
            "version": 1,
            "summary": "Root-first recursive plan",
            "components": [
                {
                    "id": "experience",
                    "name": "Experience",
                    "type": "system",
                    "responsibility": "Own interaction.",
                    "children": [
                        {"id": "workspace", "name": "Workspace", "type": "ui", "responsibility": "Render project workflows."}
                    ],
                },
                {"id": "backend", "name": "Backend", "type": "system", "responsibility": "Own request processing.", "children": [{"id": "request-handler", "name": "Request Handler", "type": "service", "responsibility": "Handle one request-processing boundary."}]},
            ],
            "relationships": [], "decisions": [], "assumptions": [], "risks": [],
        },
        "tasks": [{"title": "Build workspace", "related_component": "workspace"}],
        "reasoning": "SYSTEM_MAP, recursive scope evaluation, then reconciliation.",
        "planning_trace": {
            "system_map_root_ids": ["experience", "backend"],
            "scope_evaluations": [
                {"scope_component_id": "experience", "decomposition": "EXPANDED", "child_ids": ["workspace"]},
                {"scope_component_id": "workspace", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "The workspace is one user-facing interaction boundary with no independent subsystem below it."},
                {"scope_component_id": "backend", "decomposition": "EXPANDED", "child_ids": ["request-handler"]},
                {"scope_component_id": "request-handler", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Request Handler owns one request-processing boundary and has no independently addressable subsystem below it."},
            ],
            "reconciled": True,
        },
    }

    missing_nested = copy.deepcopy(payload)
    missing_nested["planning_trace"]["scope_evaluations"].pop(1)
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=missing_nested)
    assert rejected.status_code == 422
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 0

    fake_leaf = copy.deepcopy(payload)
    fake_leaf["planning_trace"]["scope_evaluations"][0] = {
        "scope_component_id": "experience", "decomposition": "JUSTIFIED_LEAF", "child_ids": [],
        "leaf_reason": "This deliberately incorrect leaf claim is long enough to pass only text-length validation.",
    }
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=fake_leaf)
    assert rejected.status_code == 422

    weak_leaf = copy.deepcopy(payload)
    weak_leaf["planning_trace"]["scope_evaluations"][1]["leaf_reason"] = "leaf"
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=weak_leaf)
    assert rejected.status_code == 422

    wrong_children = copy.deepcopy(payload)
    wrong_children["planning_trace"]["scope_evaluations"][0]["child_ids"] = ["backend"]
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=wrong_children)
    assert rejected.status_code == 422

    flat_root = copy.deepcopy(payload)
    flat_root["architecture"]["components"][1]["children"] = []
    flat_root["planning_trace"]["scope_evaluations"] = [
        evaluation for evaluation in flat_root["planning_trace"]["scope_evaluations"]
        if evaluation["scope_component_id"] != "request-handler"
    ]
    flat_root["planning_trace"]["scope_evaluations"][2] = {
        "scope_component_id": "backend",
        "decomposition": "JUSTIFIED_LEAF",
        "child_ids": [],
        "leaf_reason": "This long explanation deliberately attempts to keep a SYSTEM_MAP root flat despite the hierarchy contract.",
    }
    rejected = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=flat_root)
    assert rejected.status_code == 422

    accepted = client.post(f"/projects/{project_id}/interactive-initial-architecture", json=payload)
    assert accepted.status_code == 200, accepted.text
    events = client.get(f"/projects/{project_id}/events?limit=10").json()
    assert events[-1]["payload"]["planning_trace"] == payload["planning_trace"]


def test_agent_recommendation_rejects_stale_architecture_version_without_persisting_state(dsn):
    client = make_client(dsn)
    project_id = bootstrap_semantic_project(client)

    current = client.post(f"/projects/{project_id}/agent-recommendations", json={
        "recommendation": "ACCEPT_PROPOSED_CHANGE",
        "expected_architecture_version": 1,
        "reasoning": "The API responsibility should explicitly include contract validation.",
        "evidence": ["Accepted API contract now requires validation."],
        "observed_change": "The API boundary needs a metadata-only responsibility update.",
        "affected_components": ["api"],
        "proposed_changes": [{
            "operation": "update_component",
            "component_id": "api",
            "changes": {"responsibility": "Product API and contract validation"},
        }],
        "impact": "Clarifies the accepted API responsibility.",
    })
    assert current.status_code == 200, current.text
    proposal_id = current.json()["proposal"]["id"]

    accepted = client.post(f"/projects/{project_id}/architecture/proposals/{proposal_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/projects/{project_id}/architecture").json()["version"] == 2

    events_before = client.get(f"/projects/{project_id}/events?limit=100").json()
    proposals_before = client.get(f"/projects/{project_id}/architecture/proposals").json()

    stale = client.post(f"/projects/{project_id}/agent-recommendations", json={
        "recommendation": "ACCEPT_PROPOSED_CHANGE",
        "expected_architecture_version": 1,
        "reasoning": "This reasoning was prepared against Architecture v1.",
        "evidence": ["Evidence captured while v1 was current."],
        "observed_change": "A v1-era observation suggested changing the web boundary.",
        "affected_components": ["web"],
        "proposed_changes": [{
            "operation": "update_component",
            "component_id": "web",
            "changes": {"responsibility": "Workspace UI prepared from stale reasoning"},
        }],
        "impact": "Must be recomputed against the accepted v2 architecture.",
    })
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"] == {
        "code": "stale_architecture_version",
        "expected_architecture_version": 1,
        "current_architecture_version": 2,
    }
    assert client.get(f"/projects/{project_id}/events?limit=100").json() == events_before
    assert client.get(f"/projects/{project_id}/architecture/proposals").json() == proposals_before


def test_webmcp_agent_can_create_reviewable_recommendation_without_model_provider(dsn):
    client = make_client(dsn)
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
                {"id": "frontend", "name": "Frontend Experience", "type": "frontend system", "responsibility": "Own user interaction", "children": [{"id": "react-ui", "name": "React UI", "type": "ui", "responsibility": "Render application workflows"}]},
                {"id": "backend", "name": "Backend Platform", "type": "backend system", "responsibility": "Own application API", "children": [{"id": "fastapi-service", "name": "FastAPI Service", "type": "service", "responsibility": "Serve application requests"}]},
                {"id": "database", "name": "Persistence Platform", "type": "data system", "responsibility": "Own durable application state", "children": [{"id": "postgresql", "name": "PostgreSQL", "type": "database", "responsibility": "Persist application state"}]},
            ],
            "relationships": [],
            "decisions": [],
            "assumptions": [],
            "risks": [],
        },
        "tasks": [{"title": "Prepare persistence", "related_component": "postgresql"}],
        "planning_trace": {
            "system_map_root_ids": ["frontend", "backend", "database"],
            "scope_evaluations": [
                {"scope_component_id": "frontend", "decomposition": "EXPANDED", "child_ids": ["react-ui"]},
                {"scope_component_id": "react-ui", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "React UI is one user-facing rendering boundary with no independent architecture subsystem below it."},
                {"scope_component_id": "backend", "decomposition": "EXPANDED", "child_ids": ["fastapi-service"]},
                {"scope_component_id": "fastapi-service", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "FastAPI Service is one request-serving boundary with no independently addressable subsystem below it."},
                {"scope_component_id": "database", "decomposition": "EXPANDED", "child_ids": ["postgresql"]},
                {"scope_component_id": "postgresql", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "PostgreSQL is the durable persistence implementation boundary and requires no lower architecture split."},
            ],
            "reconciled": True,
        },
        "reasoning": "Host-generated recursively evaluated initial architecture.",
    })
    assert bootstrap.status_code == 200

    recommendation = client.post(f"/projects/{project_id}/agent-recommendations", json={
        "recommendation": "ACCEPT_PROPOSED_CHANGE",
        "expected_architecture_version": 1,
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
    assert architecture["components"][2]["children"][0]["name"] == "PostgreSQL"
