"""Read-only inventory of the Firestore database, ahead of the PostgreSQL move.

Answers one question before a migration script is designed: how much real data
is in there, and where. A few dozen documents and the migration is a loop; a few
hundred thousand and it needs batching, resume, and a maintenance window.

This script never writes. Run it with credentials that can read the database:

    export FIRESTORE_PROJECT_ID=<project>
    export FIRESTORE_DATABASE_ID=archbro-challenge
    gcloud auth application-default login
    python qa/count_firestore_documents.py

Collection names mirror archbro.platform.persistence.firestore (see its
__init__), so a changed ARCHBRO_FIRESTORE_PREFIX is picked up here too.
"""
from __future__ import annotations

import os
import sys

from archbro.integrations.firebase.admin import get_firestore_client

SUFFIXES = (
    "projects",
    "architectures",
    "tasks",
    "proposals",
    "events",
    "event_keys",
    "event_processing",
    "agent_runs",
    "notes",
)


def count_documents(collection) -> int:
    """Count without downloading documents, falling back if aggregation is unavailable."""
    try:
        return int(collection.count().get()[0][0].value)
    except Exception:
        return sum(1 for _ in collection.stream())


def main() -> int:
    project_id = (
        os.getenv("FIRESTORE_PROJECT_ID")
        or os.getenv("FIREBASE_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()
    if not project_id:
        print(
            "Set FIRESTORE_PROJECT_ID, FIREBASE_PROJECT_ID, or GOOGLE_CLOUD_PROJECT.",
            file=sys.stderr,
        )
        return 2

    database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
    prefix = os.getenv("ARCHBRO_FIRESTORE_PREFIX", "archbro").strip() or "archbro"
    client = get_firestore_client(project_id, database_id)

    print(f"project={project_id} database={database_id} prefix={prefix}\n")

    total = 0
    for suffix in SUFFIXES:
        name = f"{prefix}_{suffix}"
        count = count_documents(client.collection(name))
        total += count
        print(f"{name:<32} {count:>8}")
    print(f"{'TOTAL':<32} {total:>8}\n")

    print("Projects:")
    projects = list(client.collection(f"{prefix}_projects").stream())
    if not projects:
        print("  (none)")
    for snapshot in projects:
        payload = snapshot.to_dict() or {}
        data = payload.get("data", payload)
        print(f"  {snapshot.id}  name={data.get('name')!r}  status={data.get('status')!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
