from __future__ import annotations

import argparse
import json
from urllib import request

BASE_URL = "http://127.0.0.1:8012"


def api(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=15) as response:
        raw = response.read()
        return None if not raw else json.loads(raw)


def post_event(project_id: str, event_type: str, source: str, payload: dict):
    return api(
        "POST",
        f"/projects/{project_id}/events",
        {"type": event_type, "source": source, "payload": payload},
    )


def ensure_task_status(project_id: str, task: dict, target: str) -> None:
    current = task["status"]
    if current == target:
        return
    if target == "DONE" and current == "TODO":
        post_event(project_id, "TASK_UPDATED", "HUMAN", {
            "task_id": task["id"],
            "status": "IN_PROGRESS",
            "message": f"Started {task['title']} during the demo sprint.",
        })
        current = "IN_PROGRESS"
    if current != target:
        post_event(project_id, "TASK_UPDATED", "HUMAN", {
            "task_id": task["id"],
            "status": target,
            "message": f"Demo progress update: {task['title']} -> {target}.",
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-name", default="WebMCP Team Demo")
    args = parser.parse_args()

    projects = api("GET", "/projects")
    project = next((item for item in reversed(projects) if item["name"] == args.project_name), None)
    if project is None:
        raise SystemExit(f"Project not found: {args.project_name}")
    project_id = project["id"]

    proposals = api("GET", f"/projects/{project_id}/architecture/proposals")
    if any(proposal["status"] == "PENDING" for proposal in proposals):
        raise SystemExit("Interactive-decision seed expects no pending proposal yet.")

    architecture = api("GET", f"/projects/{project_id}/architecture")
    tasks = api("GET", f"/projects/{project_id}/tasks")
    component_ids: dict[str, str] = {}
    for component in architecture.get("components", []):
        haystack = " ".join(str(component.get(key, "")) for key in ("id", "name", "type", "responsibility")).lower()
        if "react" in haystack or "frontend" in haystack:
            component_ids.setdefault("frontend", component["id"])
        if "fastapi" in haystack or "backend" in haystack:
            component_ids.setdefault("backend", component["id"])
        if "postgres" in haystack or "database" in haystack or "persistence" in haystack:
            component_ids.setdefault("database", component["id"])

    missing = [role for role in ("backend", "frontend", "database") if role not in component_ids]
    if missing:
        raise SystemExit(f"Could not identify demo components from host-generated Architecture v1: {missing}")

    backend_tasks = [task for task in tasks if task.get("related_component") == component_ids["backend"]]
    frontend_tasks = [task for task in tasks if task.get("related_component") == component_ids["frontend"]]
    database_tasks = [task for task in tasks if task.get("related_component") == component_ids["database"]]
    if not backend_tasks or not frontend_tasks or not database_tasks:
        raise SystemExit("Host-generated plan must include backend, frontend, and database tasks for this demo seed.")

    for task in backend_tasks:
        ensure_task_status(project_id, task, "DONE")
    for task in frontend_tasks:
        ensure_task_status(project_id, task, "IN_PROGRESS")
    for task in database_tasks:
        ensure_task_status(project_id, task, "BLOCKED")

    recent = api("GET", f"/projects/{project_id}/events?limit=50")
    refs = {event.get("payload", {}).get("external_ref") for event in recent}

    if "github://Magic-Dala/archbro/pull/12" not in refs:
        post_event(project_id, "GITHUB_CHANGE", "GITHUB", {
            "summary": "PR #12 merged: FastAPI backend implementation and project endpoints are complete.",
            "evidence": ["CI passed", "backend API acceptance passed"],
            "external_ref": "github://Magic-Dala/archbro/pull/12",
        })

    if "slack://archbro-dev/1842" not in refs:
        post_event(project_id, "MANUAL_NOTE", "SYSTEM", {
            "external_source": "SLACK",
            "summary": "Frontend team reports the main project UI is about halfway complete; review-state polish remains.",
            "channel": "#archbro-dev",
            "external_ref": "slack://archbro-dev/1842",
        })

    if "monitoring://postgres/staging-connection-pool" not in refs:
        post_event(project_id, "MANUAL_NOTE", "SYSTEM", {
            "external_source": "MONITORING",
            "summary": "PostgreSQL staging connection pool is failing health checks and persistence work is blocked.",
            "severity": "ERROR",
            "external_ref": "monitoring://postgres/staging-connection-pool",
        })

    if "product://roadmap/offline-first-firebase" not in refs:
        post_event(project_id, "MANUAL_NOTE", "SYSTEM", {
            "external_source": "PRODUCT",
            "summary": "Approved release requirement changed: clients must support offline-first usage with automatic background synchronization, and the team must use a fully managed Firebase persistence stack for this release instead of operating PostgreSQL plus a custom realtime channel.",
            "requirement": "Offline-first clients, automatic sync, and Firebase-managed persistence are now release requirements.",
            "status": "APPROVED",
            "external_ref": "product://roadmap/offline-first-firebase",
        })

    if "slack://archbro-dev/1917" not in refs:
        post_event(project_id, "MANUAL_NOTE", "SYSTEM", {
            "external_source": "SLACK",
            "summary": "Product and platform teams confirmed the new release constraint: retire the custom WebSocket persistence path and align the client data layer with Firebase offline sync and Firestore-backed state.",
            "channel": "#archbro-dev",
            "external_ref": "slack://archbro-dev/1917",
        })

    tasks = api("GET", f"/projects/{project_id}/tasks")
    events = api("GET", f"/projects/{project_id}/events?limit=12")
    proposals = api("GET", f"/projects/{project_id}/architecture/proposals")
    print(json.dumps({
        "project_id": project_id,
        "tasks": [
            {"title": task["title"], "status": task["status"], "component": task.get("related_component")}
            for task in tasks
        ],
        "pending_reviews": [proposal["id"] for proposal in proposals if proposal["status"] == "PENDING"],
        "recent_activity": [
            {
                "type": event["type"],
                "source": event["payload"].get("external_source") or event["source"],
                "summary": event["payload"].get("summary") or event["payload"].get("message") or event["type"],
            }
            for event in events
        ],
        "next_demo_step": "Ask the WebMCP agent to use archbro_get_architecture_decision_context and submit its own recommendation.",
    }, indent=2))


if __name__ == "__main__":
    main()
