from __future__ import annotations

import json
import os
from playwright.sync_api import sync_playwright

URL = os.getenv("ARCHBRO_WEB_URL", "http://127.0.0.1:8012/")

ARCHITECTURE = {
    "version": 1,
    "summary": "React frontend calls FastAPI, backed by PostgreSQL.",
    "components": [
        {"id": "frontend", "name": "React frontend", "type": "frontend", "responsibility": "Project collaboration UI", "children": [{"id": "workspace", "name": "Workspace", "type": "ui", "responsibility": "Render project workflows"}]},
        {"id": "backend", "name": "FastAPI backend", "type": "backend", "responsibility": "REST API and orchestration", "children": [{"id": "api", "name": "API layer", "type": "service", "responsibility": "Serve project requests"}, {"id": "services", "name": "Application services", "type": "service", "responsibility": "Run project workflows"}]},
        {"id": "database", "name": "Persistence", "type": "data platform", "responsibility": "Own durable project state", "children": [{"id": "postgresql", "name": "PostgreSQL", "type": "database", "responsibility": "Persist project state"}]},
    ],
    "relationships": [
        {"source": "frontend", "target": "backend", "relationship_type": "REST", "description": "Frontend invokes backend"},
        {"source": "backend", "target": "database", "relationship_type": "PERSISTENCE", "description": "Backend persists state"},
    ],
    "decisions": ["Use React", "Use FastAPI", "Use PostgreSQL"],
    "assumptions": [],
    "risks": [],
}
TASKS = [
    {"title": "Build FastAPI backend skeleton", "related_component": "backend", "acceptance_criteria": ["API starts"]},
    {"title": "Build React frontend shell", "related_component": "frontend", "acceptance_criteria": ["UI renders"]},
    {"title": "Prepare PostgreSQL persistence", "related_component": "database", "acceptance_criteria": ["Persistence works"]},
]

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.add_init_script(
        """
        window.__archbroRegisteredTools = {};
        Object.defineProperty(document, 'modelContext', {
          configurable: true,
          value: {
            async registerTool(tool) {
              window.__archbroRegisteredTools[tool.name] = tool;
            }
          }
        });
        """
    )
    page.goto(URL, wait_until="networkidle")
    page.wait_for_function("() => Object.keys(window.__archbroRegisteredTools || {}).length >= 7")

    names = page.evaluate("() => Object.keys(window.__archbroRegisteredTools)")
    ping = json.loads(page.evaluate("async () => await window.__archbroRegisteredTools.archbro_ping.execute({})"))
    submitted = json.loads(page.evaluate(
        """async ({architecture, tasks}) => await window.__archbroRegisteredTools.archbro_bootstrap_project.execute({
          name: 'WebMCP Host Probe',
          goal: 'Build a React frontend, FastAPI backend, and PostgreSQL persistence.',
          architecture_summary: architecture.summary,
          components: architecture.components,
          relationships: architecture.relationships.map(({source, target, relationship_type, description}) => ({
            source: architecture.components.find((component) => component.id === source)?.name || source,
            target: architecture.components.find((component) => component.id === target)?.name || target,
            type: relationship_type,
            description,
          })),
          tasks: tasks.map(({title, related_component}) => ({
            title,
            component: architecture.components.find((component) => component.id === related_component)?.name || related_component,
          })),
          planning_trace: {
            system_map_root_ids: ['frontend', 'backend', 'database'],
            scope_evaluations: [
              {scope_component_id: 'frontend', decomposition: 'EXPANDED', child_ids: ['workspace']},
              {scope_component_id: 'workspace', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'The workspace is one user-facing interaction boundary with no independent architecture subsystem below it.'},
              {scope_component_id: 'backend', decomposition: 'EXPANDED', child_ids: ['api', 'services']},
              {scope_component_id: 'api', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'The API layer owns one request-boundary responsibility with no lower architecture boundary needed here.'},
              {scope_component_id: 'services', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Application services form one workflow boundary for this compact probe and need no further architecture split.'},
              {scope_component_id: 'database', decomposition: 'EXPANDED', child_ids: ['postgresql']},
              {scope_component_id: 'postgresql', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'PostgreSQL is the single durable persistence implementation boundary for this compact host probe.'}
            ],
            reconciled: true
          },
          reasoning: 'The WebMCP host planned roots, recursively evaluated every canonical scope, justified true leaves, then reconciled relationships and tasks.'
        })""",
        {"architecture": ARCHITECTURE, "tasks": TASKS},
    ))
    project_id = submitted["project"]["id"]
    decision = json.loads(page.evaluate("async () => await window.__archbroRegisteredTools.archbro_get_architecture_decision_context.execute({})"))
    brief = decision["project_brief"]

    page.evaluate("async (projectId) => fetch(`/projects/${projectId}`, {method: 'DELETE'})", project_id)

    print(json.dumps({
        "registered_tools": names,
        "tool_count": len(names),
        "ping": ping,
        "bootstrap_site_tool": "archbro_bootstrap_project",
        "bootstrap_provider": submitted["provider"],
        "bootstrap_model": submitted["model"],
        "bootstrap_built_in_model_called": submitted["built_in_model_called"],
        "architecture_version": brief["architecture"]["version"],
        "task_count": sum(brief["execution"]["counts"].values()),
        "decision_provider": decision["decision_contract"]["provider"],
        "decision_mode": decision["decision_contract"]["mode"],
    }, indent=2))
    browser.close()
