from __future__ import annotations

import json
from urllib import request

from archbro.backend.core.contracts import Task, TaskOwner, TaskSource, TaskStatus
from archbro.platform.persistence.repository import ProjectRepository

BASE_URL = "http://127.0.0.1:8012"
DB_PATH = ".webmcp-competition-live.db"
PROJECT_NAME = "WebMCP Demo"


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
        post_event(
            project_id,
            "TASK_UPDATED",
            "HUMAN",
            {
                "task_id": task["id"],
                "status": "IN_PROGRESS",
                "message": f"Started {task['title']} during the demo sprint.",
            },
        )
        current = "IN_PROGRESS"
    if current != target:
        post_event(
            project_id,
            "TASK_UPDATED",
            "HUMAN",
            {
                "task_id": task["id"],
                "status": target,
                "message": f"Demo progress update: {task['title']} -> {target}.",
            },
        )


def main() -> None:
    projects = api("GET", "/projects")
    project = next((item for item in projects if item["name"] == PROJECT_NAME), None)
    if project is None:
        raise SystemExit(f"Project not found: {PROJECT_NAME}")
    project_id = project["id"]

    repo = ProjectRepository(DB_PATH)
    existing_titles = {task.title for task in repo.list_tasks(project_id)}
    seeded_tasks = [
        Task(
            title="Wire GitHub change ingestion",
            description="Normalize repository updates into project evidence.",
            owner=TaskOwner.HUMAN,
            source=TaskSource.AGENT,
            related_component="backend",
            acceptance_criteria=["Recent GitHub changes appear in project activity"],
        ),
        Task(
            title="Polish architecture review UX",
            description="Make pending architecture decisions obvious and easy to inspect.",
            owner=TaskOwner.HUMAN,
            source=TaskSource.AGENT,
            related_component="frontend",
            acceptance_criteria=["Needs You clearly exposes pending review context"],
        ),
    ]
    for task in seeded_tasks:
        if task.title not in existing_titles:
            repo.save_task(project_id, task)

    tasks = api("GET", f"/projects/{project_id}/tasks")
    by_component = {task.get("related_component"): task for task in tasks if task.get("related_component") in {"backend", "frontend", "database"} and task["title"].startswith(("Build", "Prepare"))}
    ensure_task_status(project_id, by_component["backend"], "DONE")
    ensure_task_status(project_id, by_component["frontend"], "IN_PROGRESS")
    ensure_task_status(project_id, by_component["database"], "BLOCKED")

    recent = api("GET", f"/projects/{project_id}/events?limit=50")
    refs = {event.get("payload", {}).get("external_ref") for event in recent}

    if "github://Magic-Dala/archbro/pull/12" not in refs:
        post_event(
            project_id,
            "GITHUB_CHANGE",
            "GITHUB",
            {
                "summary": "PR #12 merged: FastAPI backend implementation and project endpoints are complete.",
                "evidence": ["CI passed", "backend API acceptance passed"],
                "external_ref": "github://Magic-Dala/archbro/pull/12",
            },
        )

    if "slack://archbro-dev/1842" not in refs:
        post_event(
            project_id,
            "MANUAL_NOTE",
            "SYSTEM",
            {
                "external_source": "SLACK",
                "summary": "Frontend team reports the main project UI is about halfway complete; review-state polish remains.",
                "channel": "#archbro-dev",
                "external_ref": "slack://archbro-dev/1842",
            },
        )

    if "monitoring://postgres/staging-connection-pool" not in refs:
        post_event(
            project_id,
            "MANUAL_NOTE",
            "SYSTEM",
            {
                "external_source": "MONITORING",
                "summary": "PostgreSQL staging connection pool is failing health checks and persistence work is blocked.",
                "severity": "ERROR",
                "external_ref": "monitoring://postgres/staging-connection-pool",
            },
        )

    proposals = api("GET", f"/projects/{project_id}/architecture/proposals")
    if not any(proposal["status"] == "PENDING" for proposal in proposals):
        post_event(
            project_id,
            "USER_MESSAGE",
            "HUMAN",
            {
                "message": "PostgreSQL is failing in staging and blocking persistence. For this demo, we propose replacing PostgreSQL with Firestore so the architecture change can be reviewed before adoption.",
                "evidence": [
                    "Monitoring: PostgreSQL connection pool health checks failing",
                    "Slack #archbro-dev: persistence work blocked",
                ],
            },
        )

    tasks = api("GET", f"/projects/{project_id}/tasks")
    proposals = api("GET", f"/projects/{project_id}/architecture/proposals")
    events = api("GET", f"/projects/{project_id}/events?limit=12")
    print(json.dumps({
        "project_id": project_id,
        "tasks": [{"title": task["title"], "status": task["status"], "component": task.get("related_component")} for task in tasks],
        "pending_reviews": [proposal["reason"] for proposal in proposals if proposal["status"] == "PENDING"],
        "recent_activity": [
            {
                "type": event["type"],
                "source": event["payload"].get("external_source") or event["source"],
                "summary": event["payload"].get("summary") or event["payload"].get("message") or event["type"],
            }
            for event in events
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
