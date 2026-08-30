from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("ARCHBRO_BASE_URL", "http://127.0.0.1:8011/")
ART = Path("qa/playwright_artifacts")
ART.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ART / "ui_prototype_report.json"

report = {
    "result": "RUNNING",
    "base_url": BASE_URL,
    "screenshots": [],
    "steps": [],
    "console_errors": [],
    "page_errors": [],
}


def step(name: str, **data):
    report["steps"].append({"name": name, **data})
    print("STEP", name, json.dumps(data, ensure_ascii=False), flush=True)


def shot(page, name: str):
    path = ART / f"ui_prototype_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    report["screenshots"].append(str(path))
    print("SHOT", path, flush=True)


def assert_healthy_page(page):
    assert page.locator("body").inner_text().strip(), "Blank page rendered"
    assert not page.locator(".error-bubble").count(), "An in-app error overlay is visible"


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    try:
        page.goto(BASE_URL, wait_until="networkidle")
        assert page.locator("#landingView").is_visible()
        assert page.locator("#landingAuthTeaser").evaluate("node => node.tagName") == "BUTTON"
        assert not page.locator("#landingAuthTeaser").locator("#landingAuthSend").count()
        assert_healthy_page(page)
        shot(page, "01_landing")

        page.locator("#landingAuthTeaser").click()
        assert page.locator("#authView").is_visible()
        assert page.locator("#landingView").is_visible(), "Auth should open as a popup over the landing page"
        assert page.locator("#authView").get_attribute("aria-modal") == "true"
        page.mouse.click(8, 8)
        assert page.locator("#landingView").is_visible(), "Clicking outside the auth popup should return to the landing page"
        assert page.evaluate("() => document.activeElement?.id") == "landingAuthTeaser"
        page.locator("#landingLoginBtn").click()
        page.keyboard.press("Escape")
        assert page.locator("#landingView").is_visible(), "Escape should return to the landing page"
        assert page.evaluate("() => document.activeElement?.id") == "landingLoginBtn"
        page.locator("#landingAuthSend").click()
        assert page.locator("#authView").is_visible()
        page.locator("#authCloseBtn").click()
        assert page.locator("#landingView").is_visible(), "Close should return to the landing page"
        assert page.evaluate("() => document.activeElement?.id") == "landingAuthSend"
        page.locator("#landingAuthTeaser").click()
        assert page.evaluate("() => localStorage.getItem('archbro-pending-goal')") is None
        assert_healthy_page(page)
        shot(page, "02_auth")

        page.locator("#authModeToggle").click()
        assert "local demo profile" in page.locator("#authSubtitle").inner_text().lower()
        page.locator("#authName").fill("Prototype Reviewer")
        page.locator("#authEmail").fill("reviewer@archbro.local")
        page.locator("#authPassword").fill("prototype-pass")
        page.locator("#authConfirmPassword").fill("prototype-pass")
        page.locator('[data-password-target="authPassword"]').click()
        assert page.locator("#authPassword").get_attribute("type") == "text"
        page.locator("#authForm button[type='submit']").click()
        assert page.locator("#preferenceView").is_visible()
        assert page.locator("#preferenceView h1").inner_text() == "What kind of project do you want to build?"
        assert_healthy_page(page)
        shot(page, "03_preference")

        page.locator('[data-project-lens="design"]').click()
        page.locator("#preferenceContinueBtn").click()
        page.locator("#workspaceHome").wait_for(state="visible")
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.keyboard.press("Escape")
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#workspaceHome").is_visible()
        page.locator("#newProjectBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        assert "name" in page.locator("#newProjectNameError").inner_text().lower()
        page.locator("#newProjectName").fill("Design systems workspace")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        page.locator("#initialGoalStage").wait_for(state="visible")
        page.locator("#initialGoalForm button[type='submit']").click()
        assert "goal" in page.locator("#initialGoalError").inner_text().lower()
        page.locator("#initialGoal").fill("Build a human-agent workspace for a design systems team.")
        page.locator("#initialGoalBackBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("[data-new-project-name-cancel]").last.click()
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#initialGoalStage").is_visible()
        assert page.locator("#initialGoal").input_value() == "Build a human-agent workspace for a design systems team."
        page.locator("#initialGoalBackBtn").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.mouse.click(5, 5)
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#initialGoalStage").is_visible()
        assert page.locator("#initialGoal").input_value() == "Build a human-agent workspace for a design systems team."
        page.locator("#initialGoalForm button[type='submit']").click()
        page.locator("#refineGoalStage").wait_for(state="visible")
        assert page.locator("#goalDraftText").input_value() == "Build a human-agent workspace for a design systems team."
        assert page.locator("#onboardingConversation").is_hidden()
        page.locator("#editOnboardingProjectName").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.keyboard.press("Escape")
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#refineGoalStage").is_visible()
        assert page.locator("#onboardingProjectName").inner_text() == "Design systems workspace"
        assert page.locator("#goalDraftText").input_value() == "Build a human-agent workspace for a design systems team."
        page.locator("#editOnboardingProjectName").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("[data-new-project-name-cancel]").last.click()
        page.locator("#newProjectNameDialog").wait_for(state="hidden")
        assert page.locator("#refineGoalStage").is_visible()
        assert page.locator("#onboardingProjectName").inner_text() == "Design systems workspace"
        assert page.locator("#goalDraftText").input_value() == "Build a human-agent workspace for a design systems team."
        page.locator("#editOnboardingProjectName").click()
        page.locator("#newProjectNameDialog").wait_for(state="visible")
        page.locator("#newProjectName").fill("Design systems workspace edited")
        page.locator("#newProjectNameDialog button[type='submit']").click()
        assert "Design systems workspace edited" in page.locator("#onboardingProjectName").inner_text()
        assert_healthy_page(page)
        shot(page, "04_progressive_onboarding")

        page.locator("#workspaceShell").wait_for(state="visible")
        assert page.locator("#projectTree").is_visible()
        assert page.locator("#notificationBtn svg[data-notification-icon]").count() == 1
        assert page.locator("#notificationBtn").get_attribute("aria-label") == "Notifications"
        assert page.locator("#workspaceSidebar .sidebar-footer").count() == 0
        assert_healthy_page(page)
        shot(page, "05_workspace")

        page.locator("#notificationBtn").click()
        assert page.locator("#notificationMenu").is_visible()
        assert "nothing needs" in page.locator("#notificationList").inner_text().lower()
        assert page.evaluate("() => document.querySelector('#notificationMenu').contains(document.activeElement)")
        shot(page, "06_notifications")
        page.keyboard.press("Escape")

        page.locator("#accountBtn").click()
        page.keyboard.press("End")
        assert page.evaluate("() => document.activeElement?.id") == "logoutBtn"
        page.keyboard.press("Home")
        assert page.evaluate("() => document.activeElement?.dataset.accountSection") == "profile"
        page.locator('[data-account-section="profile"]').click()
        page.locator("#settingsName").fill("Prototype Reviewer Updated")
        page.locator("#accountSettingsForm button[type='submit']").click()
        assert "Prototype Reviewer Updated" in page.locator("#accountBtn").get_attribute("aria-label")
        assert page.locator("#workspaceSidebar .avatar").count() == 0

        page.locator("#accountBtn").click()
        page.locator('[data-account-section="preferences"]').click()
        page.locator('#accountSettingsDialog [data-settings-lens="engineering"]').click()
        assert page.locator('#accountSettingsDialog [data-settings-lens="engineering"]').get_attribute("aria-checked") == "true"
        page.locator("#accountSettingsForm button[type='submit']").click()
        page.locator("#accountBtn").click()
        page.locator('[data-account-section="settings"]').click()
        page.locator("#settingsBlockedNotifications").uncheck()
        page.locator("#accountSettingsForm button[type='submit']").click()
        page.locator("#accountBtn").click()
        page.locator('[data-account-section="settings"]').click()
        assert not page.locator("#settingsBlockedNotifications").is_checked()
        page.locator("#accountSettingsDialog .dialog-close").click()
        assert_healthy_page(page)
        shot(page, "07_preferences_saved")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 2")
        assert page.locator("#workspaceSidebar").get_attribute("aria-hidden") == "true"
        assert page.locator("#workspaceSidebar").evaluate("node => node.inert")
        page.locator("#mobileSidebarBtn").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'newProjectBtn'")
        assert page.locator("#workspaceMain").evaluate("node => node.inert")
        for selector in ["#mobileSidebarBtn", "#newProjectBtn"]:
            box = page.locator(selector).bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, (selector, box)
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.activeElement?.id === 'mobileSidebarBtn'")
        assert_healthy_page(page)
        shot(page, "08_mobile")

        page.locator("#accountBtn").click()
        page.locator("#logoutBtn").click()
        assert page.locator("#landingView").is_visible()
        assert page.locator("#authEmail").input_value() == ""
        assert page.locator("#authPassword").input_value() == ""
        assert page.locator("#authPassword").get_attribute("type") == "password"
        assert page.locator('[data-password-target="authPassword"]').get_attribute("aria-label") == "Show password"
        assert page.locator('[data-project-lens][aria-checked="true"]').count() == 0
        assert page.locator("#preferenceContinueBtn").is_disabled()
        assert_healthy_page(page)
        shot(page, "09_logged_out")

        report["console_errors"] = list(dict.fromkeys(console_errors))
        report["page_errors"] = list(dict.fromkeys(page_errors))
        assert not console_errors
        assert not page_errors
        report["result"] = "PASS"
        step("prototype_journey_pass", screenshot_count=len(report["screenshots"]))
        print("UI_PROTOTYPE_PASS", json.dumps({"screenshots": len(report["screenshots"])}, ensure_ascii=False), flush=True)
    except Exception as exc:
        report["result"] = "FAIL"
        report["failure"] = f"{type(exc).__name__}: {exc}"
        report["console_errors"] = list(dict.fromkeys(console_errors))
        report["page_errors"] = list(dict.fromkeys(page_errors))
        try:
            shot(page, "99_failure")
        except Exception:
            pass
        print("UI_PROTOTYPE_FAIL", report["failure"], flush=True)
        raise
    finally:
        with REPORT_PATH.open("w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        context.close()
        browser.close()
