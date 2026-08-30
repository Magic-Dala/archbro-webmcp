from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, Route, sync_playwright

from playwright_diagnostics import diagnostic_scope, failure_details


BASE_URL = os.getenv("ARCHBRO_BASE_URL", "http://127.0.0.1:8011/")
ART = Path("qa/playwright_artifacts")
ART.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ART / "final_fix_report.json"


def project(project_id: str, name: str) -> dict:
    return {
        "id": project_id,
        "name": name,
        "goal": f"Build {name} with reliable human-agent collaboration.",
        "description": "Complete browser fixture for final interaction review.",
        "status": "ACTIVE",
    }


def architecture(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "version": 1,
        "summary": f"Accepted architecture for {project_id}.",
        "components": [{
            "id": f"{project_id}-experience",
            "name": "Workspace Experience",
            "type": "Frontend",
            "kind": "UI",
            "responsibility": "Keep project context visible and operable.",
            "status": "ACCEPTED",
            "children": [{
                "id": f"{project_id}-composer",
                "name": "Agent Composer",
                "type": "Interface",
                "kind": "UI",
                "responsibility": "Submit instructions with selected context.",
                "status": "ACCEPTED",
                "children": [],
            }],
        }],
        "relationships": [],
        "decisions": ["Keep the browser prototype framework-free."],
        "risks": [],
        "assumptions": [],
    }


def task(project_id: str, task_id: str, title: str, status: str = "BLOCKED") -> dict:
    return {
        "id": task_id,
        "project_id": project_id,
        "title": title,
        "description": f"{title} with complete project context.",
        "owner": "HUMAN",
        "source": "ARCHITECTURE",
        "status": status,
        "related_component": f"{project_id}-composer",
    }


def proposal(project_id: str, proposal_id: str) -> dict:
    return {
        "id": proposal_id,
        "project_id": project_id,
        "status": "PENDING",
        "reason": "Review the agent boundary",
        "observed_change": "A new external provider was requested.",
        "evidence": ["The project goal now names an external provider."],
        "impact": "The accepted architecture boundary would change.",
        "affected_components": [f"{project_id}-experience"],
        "proposed_changes": [{"component_id": f"{project_id}-experience"}],
    }


class FakeBackend:
    def __init__(self, projects: list[dict] | None = None):
        self.projects = copy.deepcopy(projects or [])
        self.contexts = {
            item["id"]: {
                "project": copy.deepcopy(item),
                "tasks": [],
                "architecture": architecture(item["id"]),
                "proposals": [],
            }
            for item in self.projects
        }
        self.fail_once: dict[tuple[str, str], int] = {}
        self.event_requests: list[dict] = []
        self.event_result = "SUCCESS"

    def fail_next(self, method: str, path: str) -> None:
        self.fail_once[(method, path)] = self.fail_once.get((method, path), 0) + 1

    def json(self, route: Route, payload, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def handle(self, route: Route) -> None:
        request = route.request
        method = request.method
        path = urlsplit(request.url).path
        key = (method, path)
        if self.fail_once.get(key, 0):
            self.fail_once[key] -= 1
            self.json(route, {"detail": "Deliberate final-fix browser failure"}, 503)
            return

        if path == "/projects" and method == "GET":
            self.json(route, self.projects)
            return
        if path == "/projects" and method == "POST":
            body = request.post_data_json
            created = project("created-project", body["name"])
            created.update({"goal": body["goal"], "description": body.get("description", "")})
            self.projects.append(created)
            self.contexts[created["id"]] = {
                "project": copy.deepcopy(created),
                "tasks": [],
                "architecture": {**architecture(created["id"]), "version": 0, "components": []},
                "proposals": [],
            }
            self.json(route, created, 201)
            return
        if path == "/onboarding/goal" and method == "POST":
            body = request.post_data_json
            current_goal = body.get("current_goal", "")
            self.json(route, {
                "goal": current_goal,
                "suggested_project_name": "Recovered Project",
                "ready": True,
                "missing_information": [],
                "assistant_message": "Your complete Goal is preserved.",
            })
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) < 2 or parts[0] != "projects":
            route.continue_()
            return
        project_id = parts[1]
        context = self.contexts.get(project_id)
        if not context:
            self.json(route, {"detail": "Project not found"}, 404)
            return

        if len(parts) == 2 and method == "GET":
            self.json(route, context["project"])
            return
        if len(parts) == 2 and method == "PATCH":
            body = request.post_data_json
            context["project"].update(body)
            next(item for item in self.projects if item["id"] == project_id).update(body)
            self.json(route, context["project"])
            return
        if parts[2:] == ["tasks"] and method == "GET":
            self.json(route, context["tasks"])
            return
        if parts[2:] == ["architecture"] and method == "GET":
            self.json(route, context["architecture"])
            return
        if parts[2:] == ["architecture", "proposals"] and method == "GET":
            self.json(route, context["proposals"])
            return
        if parts[2:] == ["events"] and method == "GET":
            self.json(route, context.get("activity", []))
            return
        if parts[2:] == ["events"] and method == "POST":
            body = request.post_data_json
            self.event_requests.append({"path": path, "body": body})
            if body.get("payload", {}).get("intent") == "INITIAL_ARCHITECTURE":
                context["architecture"] = architecture(project_id)
            result = {
                "result": self.event_result,
                "summary": "Fixture agent response.",
                "provider": "fixture",
                "model": "fixture-model",
                "actions": [],
                "architecture_review_required": False,
                "error": "Fixture agent error." if self.event_result == "ERROR" else None,
            }
            self.json(route, result)
            return
        route.continue_()


def profile(identity: str, name: object = "Review User", *, complete: bool = True, lens: object = "software", notifications: object | None = None) -> dict:
    email = identity.removeprefix("email:")
    return {
        "id": identity,
        "provider": "password",
        "email": email,
        "name": name,
        "onboardingComplete": complete,
        "defaultLens": lens,
        "notifications": notifications if notifications is not None else {"architectureApprovals": True, "blockedTasks": True},
    }


def add_storage(context: BrowserContext, *, identity: str | None = None, profiles: object | None = None, pending_goal: str | None = None, project_id: str | None = None) -> None:
    values = {}
    if identity:
        session_profile = profile(identity)
        values["archbro-demo-session"] = json.dumps({key: session_profile[key] for key in ["id", "provider", "email", "name"]})
        values["archbro-demo-profiles"] = json.dumps(profiles if profiles is not None else {identity: session_profile})
    elif profiles is not None:
        values["archbro-demo-profiles"] = json.dumps(profiles)
    if pending_goal is not None:
        values["archbro-pending-goal"] = pending_goal
    if project_id:
        values["archbro-project-id"] = project_id
    encoded = json.dumps(values).replace("</", "<\\/")
    context.add_init_script(
        f"""
        (() => {{
          if (sessionStorage.getItem('archbro-final-fix-seeded') === 'true') return;
          Object.entries({encoded}).forEach(([key, value]) => localStorage.setItem(key, value));
          sessionStorage.setItem('archbro-final-fix-seeded', 'true');
        }})();
        """
    )


def open_page(browser: Browser, backend: FakeBackend, *, viewport: dict | None = None, identity: str | None = None, profiles: object | None = None, pending_goal: str | None = None, project_id: str | None = None) -> tuple[BrowserContext, Page, list[str]]:
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 1000})
    add_storage(context, identity=identity, profiles=profiles, pending_goal=pending_goal, project_id=project_id)
    context.route("**/projects**", backend.handle)
    context.route("**/onboarding/goal", backend.handle)
    errors: list[str] = []
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE_URL, wait_until="networkidle")
    return context, page, errors


def sign_up(page: Page, email: str, name: str = "First Reviewer") -> None:
    page.locator("#landingLoginBtn").click()
    page.locator("#authModeToggle").click()
    page.locator("#authName").fill(name)
    page.locator("#authEmail").fill(email)
    page.locator("#authPassword").fill("prototype-pass")
    page.locator("#authConfirmPassword").fill("prototype-pass")
    page.locator("#authSubmitBtn").click()


def case_landing_authentication_teaser(browser: Browser) -> None:
    backend = FakeBackend()
    context, page, errors = open_page(browser, backend)
    with diagnostic_scope(context.close):
        page.locator("#landingAuthSend").click()
        assert page.locator("#authView").is_visible()
        assert page.evaluate("() => localStorage.getItem('archbro-pending-goal')") is None
        assert not errors, errors


def case_progressive_project_creation(browser: Browser) -> None:
    goal = "Keep this exact goal through project refinement and generation."
    identity = "email:pending@example.com"
    incomplete = profile(identity, complete=False, lens=None)
    backend = FakeBackend()
    context, page, errors = open_page(browser, backend, identity=identity, profiles={identity: incomplete}, pending_goal=goal)
    with diagnostic_scope(context.close):
        assert page.locator("#preferenceView").is_visible()
        assert page.evaluate("() => localStorage.getItem('archbro-pending-goal')") is None
        page.locator('[data-project-lens="design"]').click()
        page.locator("#preferenceContinueBtn").click()
        page.locator("#workspaceHome").wait_for(state="visible")
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("#newProjectName").fill("   ")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        assert page.locator("#newProjectName").input_value() == "   "
        assert "name" in page.locator("#newProjectNameError").inner_text().lower()
        page.locator("#newProjectName").fill("Durable Goal")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        page.locator("#initialGoalStage").wait_for(state="visible")
        page.locator("#initialGoal").fill("   ")
        page.locator("#initialGoalForm button[type='submit']").click()
        assert page.locator("#initialGoal").input_value() == "   "
        assert "goal" in page.locator("#initialGoalError").inner_text().lower()
        page.locator("#initialGoal").fill(goal)
        page.locator("#initialGoalForm button[type='submit']").click()
        page.locator("#refineGoalStage").wait_for(state="visible")
        assert page.locator("#goalDraftText").input_value() == goal
        assert page.locator("#onboardingConversation").is_hidden()
        ask = page.locator("#onboardingAsk")
        ask.click()
        ask.press_sequentially("Add a clear success metric to the goal.")
        page.wait_for_function("() => document.querySelector('#onboardingAsk')?.closest('.onboarding-ask')?.classList.contains('rainbow-active')")
        ask.evaluate("element => element.blur()")
        page.wait_for_function("() => !document.querySelector('#onboardingAsk')?.closest('.onboarding-ask')?.classList.contains('rainbow-active')")
        ask.focus()
        assert not page.locator("#onboardingAsk").evaluate("element => element.closest('.onboarding-ask')?.classList.contains('rainbow-active')")
        ask.press_sequentially(" Keep it measurable.")
        page.wait_for_function("() => document.querySelector('#onboardingAsk')?.closest('.onboarding-ask')?.classList.contains('rainbow-active')")
        ask.fill("")
        page.wait_for_function("() => !document.querySelector('#onboardingAsk')?.closest('.onboarding-ask')?.classList.contains('rainbow-active')")
        page.locator("#editOnboardingProjectName").click()
        page.locator("#newProjectName").fill("Durable Goal Edited")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        assert "Durable Goal Edited" in page.locator("#onboardingProjectName").inner_text()
        backend.fail_next("POST", "/projects")
        page.locator("#useGoalBtn").click()
        page.locator("#toast.error").wait_for(state="visible")
        assert page.locator("#goalDraftText").input_value() == goal
        page.locator("#useGoalBtn").click()
        page.wait_for_function("() => localStorage.getItem('archbro-project-id') === 'created-project'")
        assert page.locator('[data-project-id="created-project"] [data-project-toggle]').get_attribute("aria-expanded") == "true"
        assert page.locator('[data-project-id="created-project"] [data-project-view="overview"]').is_visible()
        page.wait_for_function("() => document.querySelector('#agentStatus')?.textContent.includes('Agent ready')")
        page.locator("#newProjectBtn").click()
        assert page.locator("#newProjectNameDialog").is_visible()
        page.keyboard.press("Escape")
        page.locator("#workspace").wait_for(state="visible")
        assert page.locator("#welcomeTitle").inner_text() == "Durable Goal Edited"
        assert not errors, errors


def case_empty_workspace_cancel(browser: Browser) -> None:
    identity = "email:empty@example.com"
    backend = FakeBackend()
    context, page, errors = open_page(browser, backend, identity=identity, profiles={identity: profile(identity)})
    with diagnostic_scope(context.close):
        assert page.locator("#workspaceHome").is_visible()
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.keyboard.press("Escape")
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#workspaceHome").is_visible()
        assert page.locator("#workspaceHomeEmpty").is_visible()
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("[data-new-project-name-cancel]").last.click()
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#workspaceHome").is_visible()
        assert not errors, errors


def case_storage_recovery(browser: Browser) -> None:
    identity = "email:shape@example.com"
    backend = FakeBackend()
    context, page, errors = open_page(browser, backend, identity=identity, profiles=None)
    with diagnostic_scope(context.close):
        page.evaluate("() => localStorage.setItem('archbro-demo-profiles', 'null')")
        page.reload(wait_until="networkidle")
        assert page.locator("#landingView").is_visible()
        assert page.evaluate("() => localStorage.getItem('archbro-demo-session')") is None
        assert page.evaluate("() => localStorage.getItem('archbro-demo-profiles')") == "null"
        assert not errors, errors

    malformed = profile(identity, name={"wrong": True}, complete=True, lens="", notifications={"architectureApprovals": "yes", "blockedTasks": False})
    context, page, errors = open_page(browser, backend, identity=identity, profiles={identity: malformed})
    with diagnostic_scope(context.close):
        assert page.locator("#preferenceView").is_visible()
        assert not errors, errors


def case_logout_reset(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Shared Alpha")])
    backend.contexts["alpha"]["tasks"] = [task("alpha", "task-a", "Resolve shared dependency")]
    context, page, errors = open_page(browser, backend)
    with diagnostic_scope(context.close):
        sign_up(page, "first@example.com")
        page.locator('[data-project-lens="engineering"]').click()
        page.locator("#preferenceContinueBtn").click()
        page.locator("#workspaceShell").wait_for(state="visible")
        page.locator("#authPassword").evaluate("input => input.type = 'text'")
        page.locator('[data-password-target="authPassword"]').evaluate("button => { button.textContent = 'Hide'; button.setAttribute('aria-label', 'Hide password'); }")
        page.locator("#accountBtn").click()
        page.locator("#logoutBtn").click()
        assert page.locator("#landingView").is_visible()
        assert page.locator("#authEmail").input_value() == ""
        assert page.locator("#authPassword").input_value() == ""
        assert page.locator("#authConfirmPassword").input_value() == ""
        assert page.locator("#authPassword").get_attribute("type") == "password"
        assert page.locator('[data-password-target="authPassword"]').get_attribute("aria-label") == "Show password"
        sign_up(page, "second@example.com", "Second Reviewer")
        assert page.locator("#preferenceView").is_visible()
        assert page.locator('[data-project-lens][aria-checked="true"]').count() == 0
        assert page.locator("#preferenceContinueBtn").is_disabled()
        page.locator('[data-project-lens="software"]').click()
        page.locator("#preferenceContinueBtn").click()
        page.locator('[data-project-id="alpha"]').wait_for(state="visible")
        assert page.locator('[data-project-open]').first.inner_text().strip() == "Shared Alpha"
        assert not errors, errors


def case_transactional_project_selection(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Alpha Project"), project("beta", "Beta Project")])
    backend.contexts["alpha"]["tasks"] = [task("alpha", "task-a", "Alpha task", "TODO")]
    backend.contexts["beta"]["tasks"] = [task("beta", "task-b", "Beta task", "TODO")]
    identity = "email:transaction@example.com"
    backend.fail_next("GET", "/projects/beta/tasks")
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        page.locator('[data-project-id="beta"] [data-project-open]').click()
        page.locator("#toast.error").wait_for(state="visible")
        assert page.evaluate("() => localStorage.getItem('archbro-project-id')") == "alpha"
        assert page.locator('[data-project-id="alpha"] [data-project-open]').get_attribute("aria-pressed") == "true"
        assert page.locator("#welcomeTitle").inner_text() == "Alpha Project"
        page.locator("#instruction").fill("Keep this event on Alpha.")
        page.locator("#instructionForm button[type='submit']").click()
        page.locator("#globalAgentReply").wait_for(state="visible")
        assert backend.event_requests[-1]["path"] == "/projects/alpha/events"
        assert not errors, errors


def case_notifications_and_context(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Attention Project")])
    backend.contexts["alpha"]["tasks"] = [task("alpha", "blocked-a", "Choose data provider")]
    backend.contexts["alpha"]["proposals"] = [proposal("alpha", "proposal-a")]
    identity = "email:attention@example.com"
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        assert page.locator("#needsCount").inner_text() == "2 items"
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectName").fill("Temporary context review")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        page.locator("#initialGoal").fill("Review the current project context before generating architecture.")
        page.locator("#initialGoalForm button[type='submit']").click()
        page.locator("#refineGoalStage").wait_for(state="visible")
        assert page.locator("#notificationBadge").is_hidden()
        page.locator("#notificationBtn").click()
        assert "nothing needs" in page.locator("#notificationList").inner_text().lower()
        page.keyboard.press("Escape")
        page.locator("#onboardingBackBtn").click()
        page.locator("#workspace").wait_for(state="visible")
        page.locator("#notificationBtn").click()
        page.locator('[data-attention-kind="task"]').click()
        assert page.locator("#view-tasks").evaluate("node => node.classList.contains('active')")
        task_context = page.locator('[data-task-select="blocked-a"]')
        assert task_context.get_attribute("aria-pressed") == "true"
        page.wait_for_function("() => document.activeElement?.dataset.taskSelect === 'blocked-a'")
        page.locator("#notificationBtn").click()
        page.locator('[data-attention-kind="proposal"]').click()
        assert page.locator("#proposalReviewDialog").is_visible()
        page.wait_for_function("() => document.activeElement?.dataset.proposalSelect === 'proposal-a'")
        page.locator("#proposalReviewDialog [data-close-dialog]").click()
        page.locator("#notificationBtn").click()
        page.locator('[data-attention-kind="task"]').evaluate("button => button.dataset.attentionId = 'missing-task'")
        page.locator('[data-attention-kind="task"]').click()
        page.locator("#notificationMenu").wait_for(state="visible")
        assert "no longer available" in page.locator("#notificationList").inner_text().lower()
        page.wait_for_function("() => document.activeElement?.textContent.includes('no longer available')")
        assert not errors, errors


def case_keyboard_and_mobile_layers(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Keyboard Project")])
    backend.contexts["alpha"]["tasks"] = [task("alpha", "task-a", "Keyboard task", "TODO")]
    backend.contexts["alpha"]["proposals"] = [proposal("alpha", "proposal-a")]
    identity = "email:keyboard@example.com"
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        assert page.locator("#projectTree").get_attribute("role") is None
        project_button = page.locator('[data-project-id="alpha"] [data-project-open]')
        assert project_button.get_attribute("aria-pressed") == "true"
        project_toggle = page.locator('[data-project-id="alpha"] [data-project-toggle]')
        project_toggle.focus()
        page.keyboard.press("Space")
        page.wait_for_function("() => document.querySelector('[data-project-id=\"alpha\"] [data-project-toggle]')?.getAttribute('aria-expanded') === 'false' && document.activeElement?.hasAttribute('data-project-toggle')")
        page.keyboard.press("Space")
        page.wait_for_function("() => document.querySelector('[data-project-id=\"alpha\"] [data-project-toggle]')?.getAttribute('aria-expanded') === 'true'")
        task_view = page.locator('[data-project-id="alpha"] [data-project-view="tasks"]')
        task_view.focus()
        page.keyboard.press("Enter")
        task_context = page.locator('[data-task-select="task-a"]')
        task_context.focus()
        page.keyboard.press("Space")
        page.wait_for_function("() => document.querySelector('[data-task-select=\"task-a\"]')?.getAttribute('aria-pressed') === 'true'")
        assert task_context.get_attribute("aria-pressed") == "true"
        assert "Keyboard task" in page.locator("#instructionContext").inner_text()

        graph_view = page.locator('[data-project-id="alpha"] [data-project-view="architecture"]')
        graph_view.focus()
        page.keyboard.press("Enter")
        graph_control = page.locator('[data-graph-node="alpha-experience"]')
        graph_control.focus()
        page.keyboard.press("Enter")
        assert "Workspace Experience" in page.locator("#selectedNode").inner_text()
        page.wait_for_function("() => document.activeElement?.dataset.graphNode === 'alpha-experience'")
        graph_drill = page.locator('[data-graph-drill="alpha-experience"]')
        graph_drill.focus()
        page.keyboard.press("Enter")
        page.locator(".graph-drilldown").wait_for(state="visible")
        page.wait_for_function("() => document.activeElement?.classList.contains('drill-back')")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.dataset.graphDrill === 'alpha-experience'")

        page.locator("#accountBtn").focus()
        page.keyboard.press("Enter")
        page.keyboard.press("End")
        assert page.evaluate("() => document.activeElement?.id") == "logoutBtn"
        page.keyboard.press("Home")
        assert page.evaluate("() => document.activeElement?.dataset.accountSection") == "profile"
        page.keyboard.press("Escape")
        assert page.evaluate("() => document.activeElement?.id") == "accountBtn"
        assert "Keyboard Project" not in (page.locator("#accountBtn").get_attribute("aria-label") or "")
        assert "Review User" in page.locator("#accountBtn").get_attribute("aria-label")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(100)
        assert page.locator("#workspaceSidebar").get_attribute("aria-hidden") == "true"
        assert page.locator("#workspaceSidebar").evaluate("node => node.inert")
        page.locator("#mobileSidebarBtn").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'newProjectBtn'")
        assert page.locator("main").evaluate("node => node.inert")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.activeElement?.id === 'mobileSidebarBtn'")
        assert page.locator("#workspaceSidebar").evaluate("node => node.inert")
        page.locator("#mobileSidebarBtn").click()
        for selector in ["#mobileSidebarBtn", '[data-project-toggle]', '[data-project-menu]', '[data-project-view="overview"]']:
            box = page.locator(selector).first.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, (selector, box)
        assert not errors, errors


def case_instruction_failure(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Instruction Project")])
    backend.contexts["alpha"]["tasks"] = [task("alpha", "task-a", "Preserve this context", "TODO")]
    backend.fail_next("POST", "/projects/alpha/events")
    identity = "email:instruction@example.com"
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        page.locator('[data-project-view="tasks"]').click()
        page.locator('[data-task-select="task-a"]').click()
        message = "Do not erase this detailed instruction after a transient failure."
        page.locator("#instruction").fill(message)
        page.locator("#instructionForm button[type='submit']").click()
        page.locator("#toast.error").wait_for(state="visible")
        assert page.locator("#instruction").input_value() == message
        assert page.evaluate("() => document.activeElement?.id") == "instruction"
        assert "Preserve this context" in page.locator("#instructionContext").inner_text()
        backend.event_result = "ERROR"
        page.locator("#instructionForm button[type='submit']").click()
        page.wait_for_function("() => document.querySelector('#globalAgentReply')?.classList.contains('error')")
        assert page.locator("#instruction").input_value() == message
        assert page.evaluate("() => document.activeElement?.id") == "instruction"
        assert "Preserve this context" in page.locator("#instructionContext").inner_text()
        assert not errors, errors


def case_inline_rename_and_account(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Rename Project")])
    identity = "email:rename@example.com"
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        menu_trigger = page.locator('[data-project-id="alpha"] [data-project-menu]')
        menu_trigger.click()
        rename = page.locator('[data-project-id="alpha"] [data-project-action="rename"]')
        rename.click()
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.activeElement?.hasAttribute('data-project-menu')")
        menu_trigger.click()
        page.locator('[data-project-id="alpha"] [data-project-action="rename"]').click()
        page.locator("[data-project-rename-input]").fill("   ")
        page.keyboard.press("Enter")
        assert "Enter a project name" in page.locator("[data-project-rename-error]").inner_text()
        backend.fail_next("PATCH", "/projects/alpha")
        page.locator("[data-project-rename-input]").fill("Still Editable")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.querySelector('[data-project-rename-error]')?.textContent.includes('503')")
        assert page.locator("[data-project-rename-input]").input_value() == "Still Editable"
        assert "503" in page.locator("[data-project-rename-error]").inner_text()
        page.locator("[data-project-rename-input]").fill("Renamed Inline")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.querySelector('[data-project-open]')?.textContent.trim() === 'Renamed Inline'")

        page.locator("#accountBtn").click()
        page.locator('[data-account-section="profile"]').click()
        page.locator("#settingsName").fill("Updated Reviewer")
        page.locator("#accountSettingsForm button[type='submit']").click()
        assert "Updated Reviewer" in page.locator("#accountBtn").get_attribute("aria-label")
        assert page.locator("#workspaceSidebar .sidebar-footer").count() == 0
        page.locator("#accountBtn").click()
        page.locator('[data-account-section="settings"]').click()
        page.locator("#settingsBlockedNotifications").uncheck()
        page.locator("#accountSettingsForm button[type='submit']").click()
        page.locator("#accountBtn").click()
        page.locator('[data-account-section="settings"]').click()
        assert not page.locator("#settingsBlockedNotifications").is_checked()
        assert not errors, errors


def case_project_row_action_menu(browser: Browser) -> None:
    backend = FakeBackend([project("alpha", "Alpha Project"), project("beta", "Beta Project")])
    identity = "email:menu@example.com"
    context, page, errors = open_page(browser, backend, identity=identity, project_id="alpha")
    with diagnostic_scope(context.close):
        trigger = page.locator('[data-project-id="alpha"] [data-project-menu]')
        trigger.wait_for(state="visible", timeout=5000)
        assert trigger.get_attribute("aria-haspopup") == "menu"
        assert trigger.get_attribute("aria-expanded") == "false"

        trigger.click()
        menu = page.locator('[data-project-id="alpha"] [data-project-menu-panel]')
        menu.wait_for(state="visible", timeout=5000)
        assert trigger.get_attribute("aria-expanded") == "true"
        menu_text = menu.inner_text().lower()
        for label in ["edit project", "rename project", "delete project"]:
            assert label in menu_text

        page.mouse.click(8, 8)
        page.wait_for_function(
            "id => !document.querySelector(`[data-project-id=\"${id}\"] [data-project-menu-panel]`) && document.querySelector(`[data-project-id=\"${id}\"] [data-project-menu]`)?.getAttribute('aria-expanded') === 'false'",
            arg="alpha",
        )
        page.wait_for_function("() => document.activeElement?.dataset.projectMenu !== undefined")

        trigger.click()
        page.locator('[data-project-id="beta"] [data-project-toggle]').click()
        page.wait_for_function("() => document.querySelector('[data-project-id=\"beta\"] [data-project-toggle]')?.getAttribute('aria-expanded') === 'true'")
        trigger = page.locator('[data-project-id="alpha"] [data-project-menu]')
        trigger.wait_for(state="visible", timeout=5000)

        trigger.click()
        menu.locator('[data-project-action="edit"]').click()
        page.locator("#editProjectDialog").wait_for(state="visible", timeout=5000)
        page.locator('[data-close-dialog="editProjectDialog"]').first.click()
        page.wait_for_function("() => !document.querySelector('#editProjectDialog')?.open")
        page.wait_for_function("() => document.activeElement?.matches('[data-project-id=\"alpha\"] [data-project-menu]')")
        trigger.click()
        menu.locator('[data-project-action="delete"]').click()
        page.locator("#deleteProjectDialog").wait_for(state="visible", timeout=5000)
        assert "alpha project" in page.locator("#deleteProjectDialog").inner_text().lower()
        page.keyboard.press("Escape")
        page.wait_for_function(
            "id => !document.querySelector(`[data-project-id=\"${id}\"] [data-project-menu-panel]`)",
            arg="alpha",
        )

        trigger.click()
        page.locator("#accountBtn").click()
        page.wait_for_function("() => !document.querySelector('[data-project-id=\"alpha\"] [data-project-menu-panel]')")
        page.wait_for_function("() => document.activeElement?.dataset.accountSection === 'profile'")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.activeElement?.id === 'accountBtn'")

        trigger.click()
        page.locator("#notificationBtn").click()
        page.wait_for_function("() => !document.querySelector('[data-project-id=\"alpha\"] [data-project-menu-panel]')")
        page.wait_for_function("() => document.activeElement?.id === 'notificationCloseBtn'")
        assert not errors, errors


CASES = [
    ("landing_authentication_teaser", case_landing_authentication_teaser),
    ("progressive_project_creation", case_progressive_project_creation),
    ("empty_workspace_cancel", case_empty_workspace_cancel),
    ("storage_recovery", case_storage_recovery),
    ("logout_reset", case_logout_reset),
    ("transactional_project_selection", case_transactional_project_selection),
    ("notifications_and_context", case_notifications_and_context),
    ("keyboard_and_mobile_layers", case_keyboard_and_mobile_layers),
    ("instruction_failure", case_instruction_failure),
    ("inline_rename_and_account", case_inline_rename_and_account),
    ("project_row_action_menu", case_project_row_action_menu),
]


report = {"result": "RUNNING", "base_url": BASE_URL, "cases": [], "failures": []}
requested = {name for name in os.getenv("ARCHBRO_FINAL_FIX_CASES", "").split(",") if name}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        for name, case in CASES:
            if requested and name not in requested:
                continue
            print("CASE", name, flush=True)
            try:
                case(browser)
                report["cases"].append({"name": name, "result": "PASS"})
                print("CASE_PASS", name, flush=True)
            except Exception as exc:
                details = getattr(exc, "archbro_failure_details", None) or failure_details(exc)
                failure = f"{details['type']}: {details['message']}"
                entry = {"name": name, "result": "FAIL", "failure": failure, **details}
                report["cases"].append(entry)
                report["failures"].append(entry.copy())
                print("CASE_FAIL", name, failure, flush=True)
                print(
                    "CASE_DIAGNOSTIC",
                    json.dumps({key: details[key] for key in ["file", "line", "assertion", "values"]}),
                    flush=True,
                )
                print(details["traceback"], flush=True)
    finally:
        browser.close()

report["result"] = "PASS" if not report["failures"] else "FAIL"
REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
if report["failures"]:
    raise AssertionError(f"{len(report['failures'])} final-fix browser case(s) failed")
print("FINAL_FIX_PLAYWRIGHT_PASS", json.dumps({"cases": len(report["cases"])}), flush=True)
