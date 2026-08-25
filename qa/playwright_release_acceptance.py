from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("ARCHBRO_BASE_URL", "http://127.0.0.1:8011/")
ART = Path("qa/playwright_artifacts")
ART.mkdir(parents=True, exist_ok=True)
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
QA_NAME = f"QA Release Rental {STAMP}"
SPARE_NAME = f"QA Selector Spare {STAMP}"

report = {
    "result": "RUNNING",
    "qa_project": None,
    "original_project": None,
    "steps": [],
    "timings": {},
    "screenshots": [],
    "console_errors": [],
    "page_errors": [],
    "http_errors": [],
    "architecture": {},
    "mobile": {},
}


def step(name: str, **data):
    item = {"name": name, **data}
    report["steps"].append(item)
    print("STEP", name, json.dumps(data, ensure_ascii=False), flush=True)


def shot(page, suffix: str):
    path = ART / f"final_{suffix}.png"
    page.screenshot(path=str(path), full_page=True)
    report["screenshots"].append(str(path))
    print("SHOT", path, flush=True)


def layout(page, name: str):
    data = page.evaluate("""
    () => ({
      viewportWidth: innerWidth,
      bodyWidth: document.body.scrollWidth,
      rootWidth: document.documentElement.scrollWidth,
      overflow: document.body.scrollWidth > innerWidth + 2 || document.documentElement.scrollWidth > innerWidth + 2,
      graphClientWidth: document.querySelector('#graphCanvas')?.clientWidth || 0,
      graphScrollWidth: document.querySelector('#graphCanvas')?.scrollWidth || 0,
    })
    """)
    report.setdefault("layout", {})[name] = data
    print("LAYOUT", name, json.dumps(data), flush=True)
    return data


def wait_ready(page, timeout=50000):
    page.wait_for_function(
        "() => document.querySelector('#agentStatus')?.textContent.includes('Agent ready')",
        timeout=timeout,
    )


def send_goal_ask(page, text: str):
    before = page.locator(".chat-message.assistant").count()
    page.locator("#onboardingAsk").fill(text)
    page.locator("#onboardingForm button[type='submit']").click()
    page.wait_for_function(
        "n => document.querySelectorAll('.chat-message.assistant').length > n || !!document.querySelector('.error-bubble')",
        arg=before,
        timeout=35000,
    )
    if page.locator(".error-bubble").count():
        retry = page.locator("#onboardingRetryBtn")
        assert retry.is_visible(), "Goal Ask failed without a retry affordance"
        retry.click()
        page.wait_for_function(
            "n => document.querySelectorAll('.chat-message.assistant').length > n && !document.querySelector('.working-bubble')",
            arg=before,
            timeout=35000,
        )
    wait_ready(page, 35000)


def all_arch_ids(components):
    ids = []
    def visit(items):
        for item in items or []:
            ids.append(item["id"])
            visit(item.get("children", []))
    visit(components)
    return ids


def max_depth(components):
    def depth(node):
        children = node.get("children") or []
        return 1 if not children else 1 + max(depth(c) for c in children)
    return max((depth(c) for c in components), default=0)


def api_json(page, path: str, method="GET", body=None):
    return page.evaluate(
        """async ({path, method, body}) => {
          const r = await fetch(path, {
            method,
            headers: {'Content-Type':'application/json'},
            body: body === null ? undefined : JSON.stringify(body),
          });
          const text = await r.text();
          let payload = null;
          try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
          return {status:r.status, ok:r.ok, payload};
        }""",
        {"path": path, "method": method, "body": body},
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.on("console", lambda msg: report["console_errors"].append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
    page.on("response", lambda res: report["http_errors"].append({"status": res.status, "url": res.url}) if res.status >= 400 else None)

    qa_project_id = None
    spare_id = None
    original_id = None
    protected_ids: set[str] = set()

    try:
        t0 = time.perf_counter()
        page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
        report["timings"]["load_s"] = round(time.perf_counter() - t0, 2)
        page.locator("#projectSelect").wait_for(state="visible", timeout=5000)
        options = page.locator("#projectSelect option")
        protected_ids = set(options.evaluate_all("opts => opts.map(o => o.value).filter(Boolean)"))
        report["protected_project_count"] = len(protected_ids)
        if options.count() and page.locator("#projectSelect").input_value():
            original_id = page.locator("#projectSelect").input_value()
            report["original_project"] = original_id
        step("existing_app_loaded", original_project=original_id, project_count=options.count())

        # NEW PROJECT + GOAL/ASK merge.
        page.locator("#newProjectBtn").click()
        page.locator("#onboardingAsk").wait_for(state="visible", timeout=5000)
        baseline = (
            "Build a smart rental platform for renters. "
            "Use Google Cloud ADK for agent orchestration and Firestore for durable user and rental state. "
            "V0 must let users search rental listings, open listing details, receive personalized recommendations, "
            "and save candidates to a shortlist. Use Google Maps location context. "
            "Keep frontend experience, agent orchestration, rental search/recommendation domain capabilities, and data/state as clear architecture boundaries."
        )
        page.locator("#goalProjectName").fill(QA_NAME)
        page.locator("#goalDraftText").fill(baseline)
        shot(page, "01_goal_manual")
        t = time.perf_counter()
        send_goal_ask(
            page,
            "Add listing verification and commute/location scoring to the V0. Preserve every existing Goal requirement and technology choice unless it conflicts with this addition.",
        )
        report["timings"]["goal_merge_s"] = round(time.perf_counter() - t, 2)
        merged_goal = page.locator("#goalDraftText").input_value()
        low_goal = merged_goal.lower()
        assert "google cloud adk" in low_goal
        assert "firestore" in low_goal
        assert "shortlist" in low_goal
        assert "verification" in low_goal or "verify" in low_goal
        assert len(merged_goal) >= len(baseline) * 0.75, "Ask appears to have replaced the existing Goal"
        page.locator("#goalProjectName").fill(QA_NAME)
        assert page.locator("#useGoalBtn").is_enabled()
        shot(page, "02_goal_merged")
        step("goal_plus_ask_merge_pass", goal_chars=len(merged_goal), seconds=report["timings"]["goal_merge_s"])

        # INITIAL ARCHITECTURE with one bounded UI retry allowed.
        t = time.perf_counter()
        page.locator("#useGoalBtn").click()
        page.wait_for_function(
            "({name, protected}) => [...document.querySelectorAll('#projectSelect option')].some(o => o.textContent === name && !protected.includes(o.value))",
            arg={"name": QA_NAME, "protected": list(protected_ids)},
            timeout=10000,
        )
        qa_project_id = page.evaluate(
            "name => [...document.querySelectorAll('#projectSelect option')].find(o => o.textContent === name)?.value || null",
            QA_NAME,
        )
        assert qa_project_id and qa_project_id not in protected_ids, (qa_project_id, protected_ids)
        page.wait_for_function("id => localStorage.getItem('archbro-project-id') === id", arg=qa_project_id, timeout=5000)
        report["qa_project"] = qa_project_id
        attempts = 1
        try:
            page.wait_for_function("() => document.querySelector('#archVersion')?.textContent.trim() === 'Version 1'", timeout=47000)
        except Exception:
            if page.locator("#generateArchitectureBtn").is_visible():
                attempts += 1
                page.locator("#generateArchitectureBtn").click()
                page.wait_for_function("() => document.querySelector('#archVersion')?.textContent.trim() === 'Version 1'", timeout=47000)
            else:
                raise
        wait_ready(page, 10000)
        report["timings"]["architecture_s"] = round(time.perf_counter() - t, 2)
        report["architecture"]["generation_attempts"] = attempts
        shot(page, "03_overview_v1")

        arch_resp = api_json(page, f"/projects/{qa_project_id}/architecture")
        assert arch_resp["ok"], arch_resp
        arch = arch_resp["payload"]
        components = arch.get("components", [])
        relationships = arch.get("relationships", [])
        ids = all_arch_ids(components)
        id_set = set(ids)
        child_count = sum(len(c.get("children") or []) for c in components)
        depth = max_depth(components)
        assert arch["version"] == 1
        assert 3 <= len(components) <= 8, len(components)
        assert len(ids) <= 40, len(ids)
        assert len(ids) == len(id_set), "duplicate architecture node ids"
        assert depth <= 3, depth
        assert child_count >= 1, "Generated architecture is still completely flat; hierarchy did not materialize"
        assert all(r["source"] in id_set and r["target"] in id_set for r in relationships)
        report["architecture"].update({
            "version": arch["version"],
            "top_level": len(components),
            "total_nodes": len(ids),
            "top_level_children": child_count,
            "depth": depth,
            "relationships": len(relationships),
        })
        tasks_resp = api_json(page, f"/projects/{qa_project_id}/tasks")
        assert tasks_resp["ok"]
        tasks = tasks_resp["payload"]
        assert len(tasks) >= 3
        assert all((t.get("related_component") is None or t.get("related_component") in id_set) for t in tasks)
        assert any(t.get("related_component") in id_set for t in tasks)
        step("architecture_contract_pass", **report["architecture"], tasks=len(tasks), seconds=report["timings"]["architecture_s"])

        # TASK start/done must stay deterministic and remain on Tasks view.
        page.locator("button[data-view='tasks']").click()
        page.locator("#taskList .task-row").first.wait_for(state="visible", timeout=5000)
        start_btn = page.locator("#taskList button[data-task-action='start']").first
        assert start_btn.is_visible()
        t = time.perf_counter()
        start_btn.click()
        page.wait_for_function("() => document.querySelectorAll('#taskList .status-pill.IN_PROGRESS').length >= 1", timeout=8000)
        assert page.locator("#view-tasks").evaluate("el => el.classList.contains('active')")
        assert "ConcurrencyException" not in page.locator("body").inner_text()
        report["timings"]["task_start_s"] = round(time.perf_counter() - t, 3)
        shot(page, "04_task_started")
        done_btn = page.locator("#taskList button[data-task-action='done']").first
        assert done_btn.is_visible()
        done_btn.click()
        page.wait_for_function("() => document.querySelectorAll('#taskList .status-pill.DONE').length >= 1", timeout=8000)
        assert page.locator("#view-tasks").evaluate("el => el.classList.contains('active')")
        shot(page, "05_task_done")
        step("task_transition_pass", start_seconds=report["timings"]["task_start_s"])

        # HEALTH MAP base + real hierarchy drilldown.
        page.locator("button[data-view='architecture']").click()
        page.locator("#graphCanvas .node-card").first.wait_for(state="visible", timeout=5000)
        node_count = page.locator("#graphCanvas .node-card").count()
        assert node_count == len(components), (node_count, len(components))
        graph_layout = layout(page, "desktop_graph")
        assert not graph_layout["overflow"], graph_layout
        shot(page, "06_health_map_clean")
        root_with_children = next(c for c in components if c.get("children"))
        page.locator(f"#graphCanvas [data-node='{root_with_children['id']}']").click()
        page.locator(".graph-drilldown").wait_for(state="visible", timeout=3000)
        drill_text = page.locator(".graph-drilldown").inner_text()
        assert root_with_children["name"] in drill_text
        assert any(child["name"] in drill_text for child in root_with_children["children"])
        shot(page, "07_hierarchy_drilldown")
        step("graph_hierarchy_pass", root=root_with_children["name"], children=len(root_with_children["children"]))

        # Force one legitimate BLOCKED task signal to verify top-level issue aggregation.
        tasks = api_json(page, f"/projects/{qa_project_id}/tasks")["payload"]
        block_task = next((t for t in tasks if t.get("status") != "DONE" and t.get("related_component") in id_set), None)
        assert block_task is not None
        block_result = api_json(
            page,
            f"/projects/{qa_project_id}/events",
            method="POST",
            body={
                "type": "TASK_UPDATED",
                "source": "HUMAN",
                "payload": {"task_id": block_task["id"], "status": "BLOCKED", "message": "QA: external dependency is blocking this task."},
            },
        )
        assert block_result["ok"] and block_result["payload"]["result"] == "SUCCESS", block_result
        page.reload(wait_until="networkidle", timeout=15000)
        page.locator("button[data-view='architecture']").click()
        page.locator("#graphCanvas .node-card").first.wait_for(state="visible", timeout=5000)
        blocked_roots = page.locator("#graphCanvas .node-card.health-blocked.attention").count()
        assert blocked_roots >= 1, "Blocked child/task did not surface at top-level health map"
        assert "need attention" in page.locator("#graphReviewState").inner_text().lower()
        shot(page, "08_health_map_blocked")
        step("health_aggregation_pass", blocked_roots=blocked_roots, blocked_task=block_task["title"])

        # REAL architecture change -> pending proposal -> accept -> version increments.
        page.locator("button[data-view='overview']").click()
        instruction = page.locator("#instruction")
        change_text = (
            "We decided to replace Firestore with Cloud SQL because relational querying is now required. "
            "Treat this as an explicit architecture requirement change and keep every unrelated architecture boundary unchanged."
        )
        proposal_attempts = 0
        for _ in range(2):
            proposal_attempts += 1
            instruction.fill(change_text)
            page.locator("#instructionForm button[type='submit']").click()
            wait_ready(page, 52000)
            page.wait_for_timeout(500)
            pending = [p for p in api_json(page, f"/projects/{qa_project_id}/architecture/proposals")["payload"] if p["status"] == "PENDING"]
            if pending:
                break
        assert pending, page.locator("#lastRun").inner_text()
        report["architecture"]["proposal_attempts"] = proposal_attempts
        proposal = pending[0]
        before_version = api_json(page, f"/projects/{qa_project_id}/architecture")["payload"]["version"]
        page.locator("button[data-view='attention']").click()
        page.locator("#proposalList .proposal-card").first.wait_for(state="visible", timeout=5000)
        shot(page, "09_needs_you")
        page.locator("#proposalList button[data-proposal='accept']").first.click()
        wait_ready(page, 10000)
        page.wait_for_timeout(500)
        after_arch = api_json(page, f"/projects/{qa_project_id}/architecture")["payload"]
        assert after_arch["version"] == before_version + 1, (before_version, after_arch["version"])
        pending_after = [p for p in api_json(page, f"/projects/{qa_project_id}/architecture/proposals")["payload"] if p["status"] == "PENDING"]
        assert not pending_after
        page.locator("button[data-view='architecture']").click()
        page.wait_for_timeout(300)
        shot(page, "10_graph_after_accept")
        step("proposal_accept_pass", before=before_version, after=after_arch["version"], attempts=proposal_attempts)

        # PROJECT EDIT. Accepted architecture locks Goal, but name/description remain editable.
        page.locator("#editProjectBtn").click()
        page.locator("#editProjectDialog").wait_for(state="visible", timeout=3000)
        assert page.locator("#editProjectGoal").is_disabled()
        edited_name = QA_NAME + " Edited"
        page.locator("#editProjectName").fill(edited_name)
        page.locator("#editProjectDescription").fill("Release acceptance project edited through the UI.")
        page.locator("#editProjectForm button[type='submit']").click()
        page.wait_for_function("name => document.querySelector('#projectSelect')?.selectedOptions[0]?.textContent === name", arg=edited_name, timeout=8000)
        assert edited_name in page.locator("#welcomeTitle").inner_text()
        shot(page, "11_project_edited")
        step("project_edit_pass", goal_locked=True)

        # Add a lightweight second project through the public API, then verify UI selector and delete-auto-switch.
        spare = api_json(page, "/projects", method="POST", body={
            "name": SPARE_NAME,
            "goal": "QA selector project. Validate project switching and deletion only.",
            "description": "Temporary release acceptance fixture.",
        })
        assert spare["ok"], spare
        spare_id = spare["payload"]["id"]
        page.reload(wait_until="networkidle", timeout=15000)
        page.locator("#projectSelect").select_option(spare_id)
        page.wait_for_function("id => localStorage.getItem('archbro-project-id') === id", arg=spare_id, timeout=5000)
        assert SPARE_NAME in page.locator("#projectSelect").locator("option:checked").inner_text()
        assert page.locator("#archVersion").inner_text().strip() == "Version 0"
        step("project_select_pass", selected=SPARE_NAME)
        page.locator("#deleteProjectBtn").click()
        page.locator("#deleteProjectDialog").wait_for(state="visible", timeout=3000)
        page.locator("#deleteProjectForm button[type='submit']").click()
        page.wait_for_function("id => ![...document.querySelectorAll('#projectSelect option')].some(o => o.value === id)", arg=spare_id, timeout=8000)
        projects_after_delete = api_json(page, "/projects")
        assert projects_after_delete["ok"]
        assert all(project["id"] != spare_id for project in projects_after_delete["payload"])
        assert page.locator("#projectSelect").input_value() != spare_id
        step("project_delete_autoswitch_pass")
        spare_id = None

        # Return to QA project and verify selector still works.
        page.locator("#projectSelect").select_option(qa_project_id)
        page.wait_for_function("id => localStorage.getItem('archbro-project-id') === id", arg=qa_project_id, timeout=5000)
        assert edited_name in page.locator("#projectSelect").locator("option:checked").inner_text()

        # MOBILE acceptance on Overview / Tasks / Graph.
        page.set_viewport_size({"width": 390, "height": 844})
        for view, suffix in [("overview", "12_mobile_overview"), ("tasks", "13_mobile_tasks"), ("architecture", "14_mobile_graph")]:
            page.locator(f"button[data-view='{view}']").click()
            page.wait_for_timeout(250)
            data = layout(page, f"mobile_{view}")
            assert not data["overflow"], data
            report["mobile"][view] = data
            shot(page, suffix)
        assert report["mobile"]["architecture"]["bodyWidth"] <= 392
        step("mobile_layout_pass", width=390)

        # No browser/runtime errors from this acceptance run.
        report["console_errors"] = list(dict.fromkeys(report["console_errors"]))
        report["page_errors"] = list(dict.fromkeys(report["page_errors"]))
        assert not report["console_errors"], report["console_errors"]
        assert not report["page_errors"], report["page_errors"]
        assert not [e for e in report["http_errors"] if e["status"] >= 500], report["http_errors"]

        report["result"] = "PASS"
        print("RELEASE_ACCEPTANCE_PASS", json.dumps({
            "qa_project": qa_project_id,
            "top_level": report["architecture"]["top_level"],
            "nodes": report["architecture"]["total_nodes"],
            "depth": report["architecture"]["depth"],
            "architecture_attempts": attempts,
            "proposal_attempts": proposal_attempts,
        }, ensure_ascii=False), flush=True)

    except Exception as exc:
        report["result"] = "FAIL"
        report["failure"] = f"{type(exc).__name__}: {exc}"
        try:
            shot(page, "99_release_failure")
        except Exception:
            pass
        print("RELEASE_ACCEPTANCE_FAIL", report["failure"], flush=True)
        raise
    finally:
        # Clean only projects created during this run. Pre-existing IDs are an immutable safety boundary.
        try:
            if spare_id and spare_id not in protected_ids:
                api_json(page, f"/projects/{spare_id}", method="DELETE")
            if qa_project_id and qa_project_id not in protected_ids:
                api_json(page, f"/projects/{qa_project_id}", method="DELETE")
            if original_id and original_id in protected_ids:
                page.evaluate("id => localStorage.setItem('archbro-project-id', id)", original_id)
            else:
                page.evaluate("() => localStorage.removeItem('archbro-project-id')")
        except Exception as cleanup_exc:
            report["cleanup_error"] = str(cleanup_exc)
        with (ART / "final_report.json").open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        context.close()
        browser.close()
