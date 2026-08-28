from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def run_gcloud(*args: str, timeout: int = 30) -> str:
    gcloud = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if not gcloud:
        raise RuntimeError("gcloud was not found on PATH")
    command_line = subprocess.list2cmdline([gcloud, *args])
    try:
        completed = subprocess.run(
            command_line,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gcloud timed out: {' '.join(args[:4])}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"gcloud failed ({completed.returncode}): {detail}")
    return completed.stdout.strip()


def request_json(
    method: str,
    url: str,
    *,
    token: str,
    quota_project: str,
    body: dict[str, Any] | None = None,
    allow_not_found: bool = False,
    timeout: int = 20,
) -> dict[str, Any] | None:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "x-goog-user-project": quota_project,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if allow_not_found and exc.code == 404:
            return None
        raise RuntimeError(
            f"Google API {method} failed with HTTP {exc.code}: {text}"
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise RuntimeError(f"Google API {method} timed out or was unreachable: {url}") from exc
    return json.loads(text) if text.strip() else None


def index_exists(indexes: list[dict[str, Any]], collection: str, second: str) -> bool:
    marker = f"/collectionGroups/{collection}/indexes/"
    for index in indexes:
        if marker not in str(index.get("name", "")):
            continue
        fields = index.get("fields") or []
        has_project = any(
            item.get("fieldPath") == "project_id" and item.get("order") == "ASCENDING"
            for item in fields
        )
        has_second = any(
            item.get("fieldPath") == second and item.get("order") == "DESCENDING"
            for item in fields
        )
        if has_project and has_second:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="keys-by-friday-2026-kbf")
    parser.add_argument("--database", default="archbro-challenge")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    rules_path = repo_root / "firestore.rules"
    if not rules_path.is_file():
        raise RuntimeError("firestore.rules was not found")

    print("phase=token", flush=True)
    token = run_gcloud("auth", "print-access-token", timeout=15)
    if not token:
        raise RuntimeError("Google access token was empty")
    project_number = run_gcloud(
        "projects", "describe", args.project, "--format=value(projectNumber)", timeout=15
    )
    if not project_number:
        raise RuntimeError("Google Cloud project number was empty")

    print("phase=ruleset-create", flush=True)
    ruleset = request_json(
        "POST",
        f"https://firebaserules.googleapis.com/v1/projects/{args.project}/rulesets",
        token=token,
        quota_project=args.project,
        body={
            "source": {
                "files": [
                    {
                        "name": "firestore.rules",
                        "content": rules_path.read_text(encoding="utf-8"),
                    }
                ]
            },
            "attachment_point": (
                f"firestore.googleapis.com/projects/{project_number}/databases/{args.database}"
            ),
        },
    )
    if not ruleset or not ruleset.get("name"):
        raise RuntimeError("Security Rules ruleset was not created")

    release_name = f"projects/{args.project}/releases/cloud.firestore/{args.database}"
    release_url = f"https://firebaserules.googleapis.com/v1/{release_name}"
    print("phase=release-read", flush=True)
    existing = request_json(
        "GET",
        release_url,
        token=token,
        quota_project=args.project,
        allow_not_found=True,
    )
    if existing is None:
        release = request_json(
            "POST",
            f"https://firebaserules.googleapis.com/v1/projects/{args.project}/releases",
            token=token,
            quota_project=args.project,
            body={"name": release_name, "rulesetName": ruleset["name"]},
        )
    else:
        release = request_json(
            "PATCH",
            release_url,
            token=token,
            quota_project=args.project,
            body={
                "release": {"name": release_name, "rulesetName": ruleset["name"]},
                "updateMask": "rulesetName",
            },
        )
    if not release or release.get("rulesetName") != ruleset["name"]:
        raise RuntimeError("Security Rules release did not reference the new ruleset")

    print("phase=indexes-list", flush=True)
    raw_indexes = run_gcloud(
        "firestore",
        "indexes",
        "composite",
        "list",
        "--project",
        args.project,
        "--database",
        args.database,
        "--format=json",
        timeout=20,
    )
    indexes: list[dict[str, Any]] = json.loads(raw_indexes or "[]")

    requested = 0
    for collection, second in (
        ("archbro_events", "data.received_at"),
        ("archbro_agent_runs", "data.completed_at"),
    ):
        if index_exists(indexes, collection, second):
            continue
        print(f"phase=index-create:{collection}", flush=True)
        run_gcloud(
            "firestore",
            "indexes",
            "composite",
            "create",
            "--project",
            args.project,
            "--database",
            args.database,
            "--collection-group",
            collection,
            "--query-scope",
            "collection",
            "--field-config=field-path=project_id,order=ascending",
            f"--field-config=field-path={second},order=descending",
            "--async",
            "--quiet",
            timeout=30,
        )
        requested += 1
        time.sleep(1)

    print(
        json.dumps(
            {
                "projectId": args.project,
                "databaseId": args.database,
                "rulesRelease": release_name,
                "rulesetPublished": True,
                "indexesRequested": requested,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
