import json
import os
from playwright.sync_api import sync_playwright

URL = os.getenv("ARCHBRO_WEB_URL", "http://127.0.0.1:8012/?mode=webmcp")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.add_init_script("""
      window.__archbroRegisteredTools = {};
      Object.defineProperty(document, 'modelContext', {
        configurable: true,
        value: { async registerTool(tool) { window.__archbroRegisteredTools[tool.name] = tool; } }
      });
    """)
    page.goto(URL, wait_until="networkidle")
    page.wait_for_function("() => Object.keys(window.__archbroRegisteredTools || {}).length >= 7")
    raw = page.evaluate("async () => await window.__archbroRegisteredTools.archbro_get_architecture_decision_context.execute({})")
    context = json.loads(raw)
    rows = [
        (
            event.get("payload", {}).get("external_source") or event.get("source"),
            event.get("payload", {}).get("summary") or event.get("payload", {}).get("message") or "",
        )
        for event in context.get("recent_activity", [])
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    assert any(source == "PRODUCT" and "offline-first" in summary for source, summary in rows)
    assert any(source == "SLACK" and "Firebase offline sync" in summary for source, summary in rows)
    browser.close()
