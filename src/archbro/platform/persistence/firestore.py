from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    Project,
    ProjectContext,
    ProjectEvent,
    ProposalStatus,
    Task,
)


TransactionRunner = Callable[[Any, Callable[[Any], Any]], Any]


def _google_transaction_runner(transaction: Any, operation: Callable[[Any], Any]) -> Any:
    """Run one Firestore transaction through the SDK-managed lifecycle."""
    from google.cloud import firestore

    return firestore.transactional(operation)(transaction)


class FirestoreProjectRepository:
    """Firestore implementation of Jim's ProjectRepositoryPort.

    The adapter keeps Archbro domain JSON intact and owns all Firestore-specific
    collection/query details. The frontend and backend core never import the
    Firestore SDK.
    """

    def __init__(
        self,
        client: Any,
        *,
        collection_prefix: str = "archbro",
        transaction_runner: TransactionRunner | None = None,
    ) -> None:
        self.client = client
        self._transaction_runner = transaction_runner or _google_transaction_runner
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

    def save_acceptance_state(
        self,
        *,
        project_id: str,
        expected_architecture_version: int,
        expected_task_updated_at: dict[str, str],
        project: Project,
        architecture: Architecture,
        tasks: list[Task],
        proposal: ArchitectureChangeProposal,
    ) -> None:
        write_count = 3 + len(tasks)
        if write_count > 500:
            raise ValueError(
                f"accepted architecture reconciliation requires {write_count} Firestore writes; maximum is 500"
            )

        architecture_ref = self._collection(self._architectures).document(project_id)
        project_ref = self._collection(self._projects).document(project.id)
        proposal_ref = self._collection(self._proposals).document(proposal.id)
        transaction = self.client.transaction()

        def commit(transaction: Any) -> None:
            architecture_snapshot = architecture_ref.get(transaction=transaction)
            current_architecture = (
                Architecture.model_validate(self._payload(architecture_snapshot))
                if architecture_snapshot.exists
                else Architecture()
            )
            if current_architecture.version != expected_architecture_version:
                raise ValueError(
                    "accepted architecture changed before proposal commit: "
                    f"expected v{expected_architecture_version}, current is v{current_architecture.version}"
                )

            project_snapshot = project_ref.get(transaction=transaction)
            current_project = Project.model_validate(self._payload(project_snapshot))
            if current_project.architecture_version != expected_architecture_version:
                raise ValueError(
                    "project architecture version changed before proposal commit: "
                    f"expected v{expected_architecture_version}, current is v{current_project.architecture_version}"
                )

            proposal_snapshot = proposal_ref.get(transaction=transaction)
            current_proposal = ArchitectureChangeProposal.model_validate(self._payload(proposal_snapshot))
            if current_proposal.status != ProposalStatus.PENDING:
                raise ValueError("proposal is no longer pending at acceptance commit")

            for task_id, expected_updated_at in expected_task_updated_at.items():
                task_snapshot = self._collection(self._tasks).document(task_id).get(transaction=transaction)
                raw_task = task_snapshot.to_dict() or {} if task_snapshot.exists else {}
                if not task_snapshot.exists or raw_task.get("project_id") != project_id:
                    raise ValueError("acceptance task changed before proposal commit")
                current_task = Task.model_validate(self._payload(task_snapshot))
                if current_task.updated_at.isoformat() != expected_updated_at:
                    raise ValueError("acceptance task changed before proposal commit")

            transaction.set(
                architecture_ref,
                {"project_id": project_id, "data": architecture.model_dump(mode="json")},
            )
            transaction.set(
                project_ref,
                {"data": project.model_dump(mode="json")},
            )
            for task in tasks:
                transaction.set(
                    self._collection(self._tasks).document(task.id),
                    {"project_id": project_id, "data": task.model_dump(mode="json")},
                )
            transaction.set(
                proposal_ref,
                {"project_id": proposal.project_id, "data": proposal.model_dump(mode="json")},
            )

        self._transaction_runner(transaction, commit)

    def save_proposal_decision(
        self,
        *,
        project_id: str,
        proposal: ArchitectureChangeProposal,
        expected_status: ProposalStatus,
    ) -> None:
        proposal_ref = self._collection(self._proposals).document(proposal.id)
        transaction = self.client.transaction()

        def commit(transaction: Any) -> None:
            snapshot = proposal_ref.get(transaction=transaction)
            current = ArchitectureChangeProposal.model_validate(self._payload(snapshot))
            if current.project_id != project_id:
                raise KeyError(proposal.id)
            if current.status != expected_status:
                raise ValueError(
                    "proposal status changed before decision commit: "
                    f"expected {expected_status.value}, current is {current.status.value}"
                )
            transaction.set(
                proposal_ref,
                {"project_id": proposal.project_id, "data": proposal.model_dump(mode="json")},
            )

        self._transaction_runner(transaction, commit)

    def save_event(self, event: ProjectEvent) -> None:
        self._collection(self._events).document(event.id).set(
            {"project_id": event.project_id, "data": event.model_dump(mode="json")}
        )

    def commit_event_actions(
        self,
        *,
        event: ProjectEvent,
        project: Project | None,
        architecture: Architecture | None,
        tasks: list[Task],
        proposals: list[ArchitectureChangeProposal],
        notes: list[str],
    ) -> None:
        write_count = 1 + int(project is not None) + int(architecture is not None) + len(tasks) + len(proposals) + len(notes)
        if write_count > 500:
            raise ValueError(f"event mutation requires {write_count} Firestore writes; maximum is 500")

        project_ref = self._collection(self._projects).document(event.project_id)
        if project is not None and project.id != event.project_id:
            raise ValueError("project mutation does not match event project")
        for proposal in proposals:
            if proposal.project_id != event.project_id:
                raise ValueError("proposal project_id mismatch during event commit")

        transaction = self.client.transaction()

        def commit(transaction: Any) -> None:
            project_snapshot = project_ref.get(transaction=transaction)
            if not project_snapshot.exists:
                raise KeyError(event.project_id)

            for task in tasks:
                task_ref = self._collection(self._tasks).document(task.id)
                task_snapshot = task_ref.get(transaction=transaction)
                if task_snapshot.exists:
                    raw = task_snapshot.to_dict() or {}
                    if raw.get("project_id") != event.project_id:
                        raise ValueError("task mutation does not match event project")

            transaction.set(
                self._collection(self._events).document(event.id),
                {"project_id": event.project_id, "data": event.model_dump(mode="json")},
            )
            if architecture is not None:
                transaction.set(
                    self._collection(self._architectures).document(event.project_id),
                    {"project_id": event.project_id, "data": architecture.model_dump(mode="json")},
                )
            if project is not None:
                transaction.set(project_ref, {"data": project.model_dump(mode="json")})
            for task in tasks:
                transaction.set(
                    self._collection(self._tasks).document(task.id),
                    {"project_id": event.project_id, "data": task.model_dump(mode="json")},
                )
            for proposal in proposals:
                transaction.set(
                    self._collection(self._proposals).document(proposal.id),
                    {"project_id": proposal.project_id, "data": proposal.model_dump(mode="json")},
                )
            for note in notes:
                note_ref = self._collection(self._notes).document(f"note_{uuid4().hex}")
                transaction.set(
                    note_ref,
                    {
                        "project_id": event.project_id,
                        "note": note,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

        self._transaction_runner(transaction, commit)

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
