from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    Project,
    ProjectContext,
    ProjectEvent,
    ProposalStatus,
    Task,
)


class FirestoreProjectRepository:
    """Firestore implementation of Jim's ProjectRepositoryPort.

    The adapter keeps Archbro domain JSON intact and owns all Firestore-specific
    collection/query details. The frontend and backend core never import the
    Firestore SDK.
    """

    def __init__(self, client: Any, *, collection_prefix: str = "archbro") -> None:
        self.client = client
        prefix = collection_prefix.strip() or "archbro"
        self._projects = f"{prefix}_projects"
        self._architectures = f"{prefix}_architectures"
        self._tasks = f"{prefix}_tasks"
        self._proposals = f"{prefix}_proposals"
        self._events = f"{prefix}_events"
        self._notes = f"{prefix}_notes"

    def _collection(self, name: str):
        return self.client.collection(name)

    @staticmethod
    def _payload(snapshot: Any) -> dict[str, Any]:
        if snapshot is None or not getattr(snapshot, "exists", False):
            raise KeyError(getattr(snapshot, "id", "missing"))
        raw = snapshot.to_dict() or {}
        data = raw.get("data")
        if not isinstance(data, dict):
            raise ValueError("Firestore Archbro document has invalid data payload")
        return data

    @staticmethod
    def _where_eq(collection: Any, field: str, value: str):
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter

            return collection.where(filter=FieldFilter(field, "==", value))
        except (ImportError, TypeError):
            return collection.where(field, "==", value)

    def _project_docs(self, collection_name: str, project_id: str) -> list[Any]:
        query = self._where_eq(self._collection(collection_name), "project_id", project_id)
        return list(query.stream())

    def save_project(self, project: Project) -> None:
        self._collection(self._projects).document(project.id).set(
            {"data": project.model_dump(mode="json")}
        )

    def get_project(self, project_id: str) -> Project:
        snapshot = self._collection(self._projects).document(project_id).get()
        try:
            return Project.model_validate(self._payload(snapshot))
        except KeyError as exc:
            raise KeyError(project_id) from exc

    def list_projects(self) -> list[Project]:
        projects = [
            Project.model_validate(self._payload(snapshot))
            for snapshot in self._collection(self._projects).stream()
        ]
        return sorted(projects, key=lambda project: project.created_at, reverse=True)

    def delete_project(self, project_id: str) -> bool:
        project_ref = self._collection(self._projects).document(project_id)
        if not project_ref.get().exists:
            return False
        self._collection(self._architectures).document(project_id).delete()
        for collection_name in (self._tasks, self._proposals, self._events, self._notes):
            for snapshot in self._project_docs(collection_name, project_id):
                snapshot.reference.delete()
        project_ref.delete()
        return True

    def save_architecture(self, project_id: str, architecture: Architecture) -> None:
        self._collection(self._architectures).document(project_id).set(
            {"project_id": project_id, "data": architecture.model_dump(mode="json")}
        )

    def get_architecture(self, project_id: str) -> Architecture:
        snapshot = self._collection(self._architectures).document(project_id).get()
        if not snapshot.exists:
            return Architecture()
        return Architecture.model_validate(self._payload(snapshot))

    def save_task(self, project_id: str, task: Task) -> None:
        self._collection(self._tasks).document(task.id).set(
            {"project_id": project_id, "data": task.model_dump(mode="json")}
        )

    def get_task(self, task_id: str) -> Task:
        snapshot = self._collection(self._tasks).document(task_id).get()
        try:
            return Task.model_validate(self._payload(snapshot))
        except KeyError as exc:
            raise KeyError(task_id) from exc

    def list_tasks(self, project_id: str) -> list[Task]:
        tasks = [Task.model_validate(self._payload(doc)) for doc in self._project_docs(self._tasks, project_id)]
        return sorted(tasks, key=lambda task: task.created_at)

    def save_proposal(self, proposal: ArchitectureChangeProposal) -> None:
        self._collection(self._proposals).document(proposal.id).set(
            {"project_id": proposal.project_id, "data": proposal.model_dump(mode="json")}
        )

    def get_proposal(self, proposal_id: str) -> ArchitectureChangeProposal:
        snapshot = self._collection(self._proposals).document(proposal_id).get()
        try:
            return ArchitectureChangeProposal.model_validate(self._payload(snapshot))
        except KeyError as exc:
            raise KeyError(proposal_id) from exc

    def list_proposals(self, project_id: str) -> list[ArchitectureChangeProposal]:
        proposals = [
            ArchitectureChangeProposal.model_validate(self._payload(doc))
            for doc in self._project_docs(self._proposals, project_id)
        ]
        return sorted(proposals, key=lambda proposal: proposal.created_at)

    def save_event(self, event: ProjectEvent) -> None:
        self._collection(self._events).document(event.id).set(
            {"project_id": event.project_id, "data": event.model_dump(mode="json")}
        )

    def add_note(self, project_id: str, note: str) -> None:
        self._collection(self._notes).add(
            {
                "project_id": project_id,
                "note": note,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def list_notes(self, project_id: str, limit: int = 20) -> list[str]:
        records: list[tuple[str, str]] = []
        for snapshot in self._project_docs(self._notes, project_id):
            raw = snapshot.to_dict() or {}
            note = raw.get("note")
            if isinstance(note, str):
                records.append((str(raw.get("created_at", "")), note))
        records.sort(key=lambda item: item[0])
        return [note for _, note in records[-limit:]]

    def load_context(self, project_id: str) -> ProjectContext:
        return ProjectContext(
            project=self.get_project(project_id),
            architecture=self.get_architecture(project_id),
            tasks=self.list_tasks(project_id),
            pending_proposals=[
                proposal
                for proposal in self.list_proposals(project_id)
                if proposal.status == ProposalStatus.PENDING
            ],
            recent_notes=self.list_notes(project_id),
        )

    def snapshot(self, project_id: str) -> str:
        return json.dumps(
            self.load_context(project_id).model_dump(mode="json"),
            sort_keys=True,
        )
