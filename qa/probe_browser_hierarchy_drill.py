from __future__ import annotations

import json
import os
from urllib import error, request
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("ARCHBRO_BASE_URL", "http://127.0.0.1:8012/")
CODE_REVISION = "0123456789abcdef0123456789abcdef01234567"


def api(method: str, path: str, payload: dict | None = None) -> dict | None:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        urljoin(BASE_URL, path.lstrip("/")),
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        raise AssertionError(f"{method} {path} failed: {exc.code} {exc.read().decode('utf-8', errors='replace')}") from exc


def diagram_request(url: str, project_id: str) -> dict | None:
    parsed = urlsplit(url)
    if parsed.path != f"/projects/{project_id}/architecture/diagram":
        return None
    query = parse_qs(parsed.query)
    return {
        "scope": query.get("scope", [None])[0],
        "expected_architecture_version": query.get("expected_architecture_version", [None])[0],
        "reading_mode": query.get("reading_mode", [None])[0],
        "url": url,
    }


def bootstrap_project() -> tuple[str, int]:
    project = api(
        "POST",
        "/projects",
        {
            "name": "Real Browser Hierarchy Drill Acceptance",
            "goal": "Prove the human architecture graph drills one backend-authored scope at a time.",
            "description": "Temporary browser acceptance project.",
        },
    )
    assert project
    project_id = project["id"]
    result = api(
        "POST",
        f"/projects/{project_id}/interactive-initial-architecture",
        {
            "architecture": {
                "version": 1,
                "summary": "Outside-in hierarchy used only for real browser drill acceptance.",
                "components": [
                    {
                        "id": "experience",
                        "name": "Product Experience",
                        "type": "system",
                        "kind": "SYSTEM",
                        "responsibility": "Own the human-facing product experience.",
                        "children": [
                            {
                                "id": "workspace",
                                "name": "Project Workspace",
                                "type": "ui",
                                "kind": "UI",
                                "responsibility": "Render project and architecture workflows.",
                                "children": [],
                            }
                        ],
                    },
                    {
                        "id": "backend",
                        "name": "Backend Services",
                        "type": "system",
                        "kind": "SYSTEM",
                        "responsibility": "Own product and agent-facing application services.",
                        "children": [
                            {
                                "id": "api",
                                "name": "Architecture API",
                                "type": "service",
                                "kind": "SERVICE",
                                "responsibility": "Serve scoped architecture projections.",
                                "children": [
                                    {
                                        "id": "projection",
                                        "name": "Projection Engine",
                                        "type": "service",
                                        "kind": "SERVICE",
                                        "responsibility": "Derive one canonical architecture scope at a time.",
                                        "children": [],
                                    }
                                ],
                            },
                            {
                                "id": "review",
                                "name": "Review Workflow",
                                "type": "service",
                                "kind": "SERVICE",
                                "responsibility": "Keep architecture changes human reviewable.",
                                "children": [],
                            },
                        ],
                    },
                    {
                        "id": "data",
                        "name": "Durable State",
                        "type": "system",
                        "kind": "SYSTEM",
                        "responsibility": "Persist accepted project state.",
                        "children": [
                            {
                                "id": "architecture_repository",
                                "name": "Architecture Repository",
                                "type": "database",
                                "kind": "DATA_STORE",
                                "responsibility": "Persist canonical architecture versions.",
                                "children": [],
                            }
                        ],
                    },
                ],
                "relationships": [
                    {
                        "source": "workspace",
                        "target": "api",
                        "relationship_type": "HTTPS",
                        "description": "Workspace requests architecture projections.",
                    },
                    {
                        "source": "projection",
                        "target": "architecture_repository",
                        "relationship_type": "READ",
                        "description": "Projection reads the accepted canonical architecture.",
                    },
                ],
                "decisions": [],
                "assumptions": [],
                "risks": [],
            },
            "tasks": [
                {
                    "title": "Validate hierarchical architecture UI",
                    "description": "Exercise real browser scope navigation.",
                    "related_component": "projection",
                    "source": "AGENT",
                    "acceptance_criteria": [],
                    "dependencies": [],
                }
            ],
            "planning_trace": {
                "system_map_root_ids": ["experience", "backend", "data"],
                "scope_evaluations": [
                    {"scope_component_id": "experience", "decomposition": "EXPANDED", "child_ids": ["workspace"]},
                    {"scope_component_id": "workspace", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Project Workspace is one user interaction boundary with no independent architecture subsystem below it."},
                    {"scope_component_id": "backend", "decomposition": "EXPANDED", "child_ids": ["api", "review"]},
                    {"scope_component_id": "api", "decomposition": "EXPANDED", "child_ids": ["projection"]},
                    {"scope_component_id": "projection", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Projection Engine owns one canonical projection responsibility with no lower architecture boundary required."},
                    {"scope_component_id": "review", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Review Workflow owns one human-governance boundary with no independent subsystem below it."},
                    {"scope_component_id": "data", "decomposition": "EXPANDED", "child_ids": ["architecture_repository"]},
                    {"scope_component_id": "architecture_repository", "decomposition": "JUSTIFIED_LEAF", "child_ids": [], "leaf_reason": "Architecture Repository is one durable canonical persistence boundary with no lower architecture split."},
                ],
                "reconciled": True,
            },
            "reasoning": "A recursively evaluated three-level fixture proves that real clicks request progressively deeper backend scopes.",
        },
    )
    assert result and result["architecture"]["version"] == 1
    code_snapshot = api(
        "POST",
        f"/projects/{project_id}/code-architecture/snapshots",
        {
            "repository": "Magic-Dala/archbro",
            "revision": CODE_REVISION,
            "summary": "Browser workspace calls the architecture API, which reads the project repository.",
            "components": [
                {
                    "id": "web",
                    "name": "Browser Workspace",
                    "type": "frontend",
                    "responsibility": "Render architecture workflows.",
                    "kind": "UI",
                    "source_evidence_ids": ["web-entry"],
                },
                {
                    "id": "backend",
                    "name": "Backend Runtime",
                    "type": "backend",
                    "responsibility": "Own product architecture APIs.",
                    "kind": "SYSTEM",
                    "source_evidence_ids": ["api-entry"],
                    "children": [
                        {
                            "id": "api",
                            "name": "Architecture API",
                            "type": "service",
                            "responsibility": "Serve scoped architecture projections.",
                            "kind": "SERVICE",
                            "source_evidence_ids": ["api-entry"],
                        },
                        {
                            "id": "repository",
                            "name": "Project Repository",
                            "type": "data-access",
                            "responsibility": "Read persisted project architecture state.",
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
                    "description": "The browser requests architecture data.",
                    "source_evidence_ids": ["web-entry", "api-entry"],
                },
                {
                    "source": "api",
                    "target": "repository",
                    "relationship_type": "CALL",
                    "description": "The API reads canonical project state.",
                    "source_evidence_ids": ["api-entry", "repo-entry"],
                },
            ],
            "source_evidence": [
                {
                    "id": "web-entry",
                    "path": "frontend/web/app.js",
                    "line_start": 1,
                    "line_end": 2,
                    "excerpt": "import {getFirebaseIdToken} from './firebase-auth.js';\nconst prototype = window.ArchbroPrototype;",
                    "symbol": "ArchbroPrototype",
                },
                {
                    "id": "api-entry",
                    "path": "src/archbro/backend/api/agent_surface.py",
                    "line_start": 94,
                    "line_end": 95,
                    "excerpt": "@router.post(\"/projects/{project_id}/code-architecture/snapshot\")\nasync def build_repository_code_architecture(",
                    "symbol": "build_repository_code_architecture",
                },
                {
                    "id": "repo-entry",
                    "path": "src/archbro/backend/core/repository.py",
                    "line_start": 1,
                    "line_end": 1,
                    "excerpt": "from __future__ import annotations",
                    "symbol": "ProjectRepositoryPort",
                },
            ],
        },
    )
    assert code_snapshot and code_snapshot["derived_artifact_persisted"] is True
    return project_id, 1


def storage_seed(project_id: str) -> str:
    identity = "email:hierarchy-browser-acceptance@archbro.local"
    profile = {
        "id": identity,
        "provider": "password",
        "email": "hierarchy-browser-acceptance@archbro.local",
        "name": "Hierarchy Browser Acceptance",
        "onboardingComplete": True,
        "defaultLens": "software",
        "notifications": {"architectureApprovals": True, "blockedTasks": True},
    }
    values = {
        "archbro-demo-session": json.dumps({key: profile[key] for key in ["id", "provider", "email", "name"]}),
        "archbro-demo-profiles": json.dumps({identity: profile}),
        "archbro-project-id": project_id,
    }
    return json.dumps(values).replace("</", "<\\/")


def main() -> int:
    project_id, version = bootstrap_project()
    root_payload = api("GET", f"/projects/{project_id}/architecture/diagram?expected_architecture_version={version}")
    assert root_payload
    root_pairs = [(edge["source"], edge["target"]) for edge in root_payload["diagram"]["edges"]]
    assert len(root_pairs) == len(set(root_pairs)), root_pairs
    diagram_requests: list[dict] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[dict] = []
    trace: list[dict] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            encoded = storage_seed(project_id)
            context.add_init_script(
                f"""
                (() => {{
                  Object.entries({encoded}).forEach(([key, value]) => localStorage.setItem(key, value));
                }})();
                """
            )
            page = context.new_page()
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            def on_request(req) -> None:
                parsed = diagram_request(req.url, project_id)
                if parsed is not None:
                    diagram_requests.append(parsed)

            def on_response(response) -> None:
                if response.status >= 400:
                    http_errors.append({"status": response.status, "url": response.url})

            page.on("request", on_request)
            page.on("response", on_response)
            page.goto(BASE_URL, wait_until="networkidle")
            page.locator("#workspaceShell").wait_for(state="visible")
            page.locator(f'[data-project-id="{project_id}"] [data-project-view="architecture"]').click()
            page.locator("#view-architecture").wait_for(state="visible")

            root = page.locator('[data-component="backend"]')
            root.wait_for(state="visible")
            assert root.get_attribute("data-node-action") == "drill"
            assert "is-drill" in (root.get_attribute("class") or "")
            assert root.get_attribute("data-child-count") == "2"
            assert "OPEN · 2 CHILDREN" in (root.text_content() or "")
            trace.append({"scope": "ROOT", "visible": ["experience", "backend", "data"]})
            assert page.locator('[data-projection-role="CONTEXT"]').count() == 0
            if page.locator(".graph-edge text").count():
                page.locator('button[data-reading-mode="READ"]').click()
                page.wait_for_function("() => document.querySelector('.graph-stage')?.dataset.readingMode === 'READ'")
                assert all(float(value) == 0 for value in page.locator(".graph-edge text").evaluate_all("els => els.map(el => getComputedStyle(el).opacity)"))
                page.locator('button[data-reading-mode="FULL"]').click()
                page.wait_for_function("() => document.querySelector('.graph-stage')?.dataset.readingMode === 'FULL'")
                assert any(float(value) > 0 for value in page.locator(".graph-edge text").evaluate_all("els => els.map(el => getComputedStyle(el).opacity)"))
                page.locator('button[data-reading-mode="MAP"]').click()
                page.wait_for_function("() => document.querySelector('.graph-stage')?.dataset.readingMode === 'MAP'")

            assert diagram_requests, "initial project load must request the root backend diagram"
            assert diagram_requests[-1]["scope"] is None
            assert diagram_requests[-1]["expected_architecture_version"] == str(version)

            root.click()
            assert "selected" in (root.get_attribute("class") or "")
            assert page.locator('[data-component="backend"][data-projection-role="SCOPE"]').count() == 0
            root.dblclick()
            page.locator('[data-component="api"]').wait_for(state="visible")
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Backend Services'")
            assert diagram_requests[-1]["scope"] == "backend"
            assert diagram_requests[-1]["expected_architecture_version"] == str(version)
            assert page.locator(".graph-stage").get_attribute("data-reading-mode") == "MAP"
            scope_anchor = page.locator('[data-projection-role="SCOPE"]')
            assert scope_anchor.count() == 0, "current scope is represented by the scope bar, not duplicated as a graph node"
            primary = page.locator('[data-projection-role="PRIMARY"]')
            assert {primary.nth(index).get_attribute("data-component") for index in range(primary.count())} == {"api", "review"}
            assert page.locator('[data-projection-role="CONTEXT"]').count() == 0
            assert page.locator('.graph-hierarchy').count() == 0
            assert page.locator('.graph-edge').count() == 0
            assert {page.locator('[data-node]').nth(index).get_attribute('data-component') for index in range(page.locator('[data-node]').count())} == {"api", "review"}
            text_overflow = page.locator('#graphCanvas .node-card').evaluate_all("""cards => cards.flatMap(card => { const surface = card.querySelector('.node-surface')?.getBBox(); if (!surface) return []; return [...card.querySelectorAll('text')].map(text => ({text: text.textContent, box: text.getBBox()})).filter(item => item.box.x < surface.x - 1 || item.box.y < surface.y - 1 || item.box.x + item.box.width > surface.x + surface.width + 1 || item.box.y + item.box.height > surface.y + surface.height + 1); })""")
            assert not text_overflow, text_overflow
            trace.append({"scope": "backend", "primary": ["api", "review"], "hierarchy_connectors": page.locator('.graph-hierarchy').count()})

            api_node = page.locator('[data-component="api"]')
            assert api_node.get_attribute("data-node-action") == "drill"
            assert "OPEN · 1 CHILD" in (api_node.text_content() or "")
            api_node.click()
            assert "selected" in (api_node.get_attribute("class") or "")
            assert page.locator('[data-component="api"][data-projection-role="SCOPE"]').count() == 0
            api_node.dblclick()
            leaf = page.locator('[data-component="projection"]')
            leaf.wait_for(state="visible")
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Architecture API'")
            assert diagram_requests[-1]["scope"] == "api"
            assert diagram_requests[-1]["expected_architecture_version"] == str(version)
            api_scope_anchor = page.locator('[data-projection-role="SCOPE"]')
            assert api_scope_anchor.count() == 0
            assert page.locator('[data-projection-role="CONTEXT"]').count() == 0
            assert page.locator('.graph-hierarchy').count() == 0
            assert page.locator('.graph-edge').count() == 0
            assert {page.locator('[data-node]').nth(index).get_attribute('data-component') for index in range(page.locator('[data-node]').count())} == {"projection"}
            assert leaf.get_attribute("data-node-action") == "inspect"
            trace.append({"scope": "api", "primary": ["projection"]})

            before_leaf = len(diagram_requests)
            leaf.click()
            page.wait_for_function("() => document.querySelector('#selectedNode')?.textContent.includes('Projection Engine')")
            assert len(diagram_requests) == before_leaf, "leaf inspection must not fetch another hierarchy scope"

            before_modes = len(diagram_requests)
            for mode in ["MAP", "READ", "FULL"]:
                page.locator(f'button[data-reading-mode="{mode}"]').click()
                page.wait_for_function(f"() => document.querySelector('.graph-stage')?.dataset.readingMode === '{mode}'")
                assert diagram_requests[-1]["reading_mode"] == mode
            assert len(diagram_requests) == before_modes + 2, "READ and FULL must request their own routed projections; MAP was already active"

            page.locator("[data-graph-back]").click()
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Backend Services'")
            page.locator('[data-component="api"]').wait_for(state="visible")
            assert diagram_requests[-1]["scope"] == "backend"
            page.locator("[data-graph-back]").click()
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Overview'")
            page.locator('[data-component="backend"]').wait_for(state="visible")
            assert diagram_requests[-1]["scope"] is None

            page.locator('[data-component="backend"]').dblclick()
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Backend Services'")
            page.locator('[data-component="api"]').wait_for(state="visible")
            page.locator('[data-scope-target=""]').click()
            page.wait_for_function("() => document.querySelector('.graph-scope-copy strong')?.textContent === 'Overview'")
            page.locator('[data-component="backend"]').wait_for(state="visible")
            assert diagram_requests[-1]["scope"] is None

            before_code_mode = len(diagram_requests)
            page.locator('[data-architecture-graph-kind="code"]').click()
            page.locator('[data-code-node="code-node:api"]').wait_for(state="visible")
            assert page.locator("#architectureViewTitle").text_content() == "Code Graph"
            assert page.locator("#graphVersion").text_content() == f"@{CODE_REVISION[:8]}"
            snapshot_bar = page.locator(".code-snapshot-bar")
            assert "Magic-Dala/archbro" in (snapshot_bar.text_content() or "")
            assert CODE_REVISION in (snapshot_bar.text_content() or "")
            assert len(diagram_requests) == before_code_mode, "switching to durable Code Architecture must not infer or refetch Living topology"

            page.locator('[data-code-node="code-node:api"]').click()
            page.wait_for_function("() => document.querySelector('#selectedNode')?.textContent.includes('Architecture API')")
            evidence_link = page.locator('#nodeEvidence a[href*="src/archbro/backend/api/agent_surface.py"]').first
            evidence_link.wait_for(state="visible")
            href = evidence_link.get_attribute("href") or ""
            assert f"/blob/{CODE_REVISION}/" in href
            assert href.endswith("#L94-L95")
            assert "build_repository_code_architecture" in (page.locator("#nodeEvidence").text_content() or "")
            trace.append({"mode": "code", "repository": "Magic-Dala/archbro", "revision": CODE_REVISION, "selected": "code-node:api"})

            page.locator('[data-architecture-graph-kind="living"]').click()
            page.locator('[data-component="backend"]').wait_for(state="visible")
            assert page.locator("#architectureViewTitle").text_content() == "Living Graph"

            assert not page_errors, page_errors
            assert not console_errors, console_errors
            assert not http_errors, http_errors
            browser.close()

        scopes = [item["scope"] for item in diagram_requests]
        required_subsequence = [None, "backend", "api", "backend", None, "backend", None]
        cursor = 0
        for scope in scopes:
            if cursor < len(required_subsequence) and scope == required_subsequence[cursor]:
                cursor += 1
        assert cursor == len(required_subsequence), (scopes, required_subsequence)
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "architecture_version": version,
                    "diagram_scope_sequence": scopes,
                    "trace": trace,
                    "page_errors": page_errors,
                    "console_errors": console_errors,
                    "http_errors": http_errors,
                    "result": "PASS",
                },
                indent=2,
            )
        )
        return 0
    finally:
        try:
            api("DELETE", f"/projects/{project_id}")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
