from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable
from uuid import uuid4

from archbro.backend.core.contracts import (
    AgentRunResult,
    Architecture,
    ArchitectureChangeProposal,
    ObservationClaim,
    ObservationClaimState,
    Project,
    ProjectContext,
    ProjectEvent,
    ProposalStatus,
    Task,
)
from archbro.backend.core.observation import ObservationMutationPlan, ObservationRejectedError


_OBSERVATION_CLAIM_TTL = timedelta(minutes=2)


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
        self._event_keys = f"{prefix}_event_keys"
        self._event_processing = f"{prefix}_event_processing"
        self._agent_runs = f"{prefix}_agent_runs"
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

    @staticmethod
    def _source_key(event: ProjectEvent) -> str | None:
        if not event.source_event_id:
            return None
        raw = f"{event.project_id}|{event.source.value}|{event.source_event_id}".encode("utf-8")
        return sha256(raw).hexdigest()

    @staticmethod
    def _same_observation(left: ProjectEvent, right: ProjectEvent) -> bool:
        return (
            left.project_id == right.project_id
            and left.source == right.source
            and left.type == right.type
            and left.payload == right.payload
            and (
                left.source_event_id == right.source_event_id
                or left.source_event_id is None
                or right.source_event_id is None
            )
        )

    def _bounded_project_docs(
        self,
        collection_name: str,
        project_id: str,
        *,
        order_field: str,
        limit: int,
    ) -> list[Any]:
        if limit <= 0:
            return []
        query = self._where_eq(self._collection(collection_name), "project_id", project_id)
        query = query.order_by(order_field, direction="DESCENDING").limit(limit)
        return list(query.stream())

    @staticmethod
    def _claim_is_stale(updated_at: str) -> bool:
        try:
            claimed_at = datetime.fromisoformat(updated_at)
        except (TypeError, ValueError):
            return True
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - claimed_at > _OBSERVATION_CLAIM_TTL

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
        for collection_name in (
            self._tasks,
            self._proposals,
            self._events,
            self._event_keys,
            self._event_processing,
            self._agent_runs,
            self._notes,
        ):
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
        source_key = self._source_key(event)
        if source_key is None:
            self._collection(self._events).document(event.id).set(
                {"project_id": event.project_id, "data": event.model_dump(mode="json")}
            )
            return

        transaction = self.client.transaction()
        key_ref = self._collection(self._event_keys).document(source_key)

        def commit(transaction: Any) -> None:
            key_snapshot = key_ref.get(transaction=transaction)
            if key_snapshot.exists:
                raw_key = key_snapshot.to_dict() or {}
                existing_event_id = str(raw_key.get("event_id", ""))
                existing_snapshot = self._collection(self._events).document(existing_event_id).get(
                    transaction=transaction
                )
                existing_event = ProjectEvent.model_validate(self._payload(existing_snapshot))
                if not self._same_observation(existing_event, event):
                    raise ObservationRejectedError(
                        "source event id is already registered with different observation data"
                    )
                return

            event_ref = self._collection(self._events).document(event.id)
            existing_snapshot = event_ref.get(transaction=transaction)
            if existing_snapshot.exists:
                existing_event = ProjectEvent.model_validate(self._payload(existing_snapshot))
                if not self._same_observation(existing_event, event):
                    raise ObservationRejectedError(
                        "event id is already registered with different observation data"
                    )
            else:
                transaction.set(
                    event_ref,
                    {"project_id": event.project_id, "data": event.model_dump(mode="json")},
                )
            transaction.set(
                key_ref,
                {"project_id": event.project_id, "event_id": event.id},
            )

        self._transaction_runner(transaction, commit)

    def get_event(self, event_id: str) -> ProjectEvent:
        snapshot = self._collection(self._events).document(event_id).get()
        try:
            return ProjectEvent.model_validate(self._payload(snapshot))
        except KeyError as exc:
            raise KeyError(event_id) from exc

    def list_events(self, project_id: str, limit: int = 100) -> list[ProjectEvent]:
        events = [
            ProjectEvent.model_validate(self._payload(doc))
            for doc in self._bounded_project_docs(
                self._events,
                project_id,
                order_field="data.received_at",
                limit=limit,
            )
        ]
        return list(reversed(events))

    def list_agent_runs(self, project_id: str, limit: int = 100) -> list[AgentRunResult]:
        runs = [
            AgentRunResult.model_validate(self._payload(doc))
            for doc in self._bounded_project_docs(
                self._agent_runs,
                project_id,
                order_field="data.completed_at",
                limit=limit,
            )
        ]
        return list(reversed(runs))

    def claim_observation(self, event: ProjectEvent, *, run_id: str) -> ObservationClaim:
        transaction = self.client.transaction()

        def commit(transaction: Any) -> ObservationClaim:
            project_ref = self._collection(self._projects).document(event.project_id)
            if not project_ref.get(transaction=transaction).exists:
                raise KeyError(event.project_id)

            source_key = self._source_key(event)
            canonical_event_id = event.id
            key_ref = None
            key_snapshot = None
            if source_key is not None:
                key_ref = self._collection(self._event_keys).document(source_key)
                key_snapshot = key_ref.get(transaction=transaction)
                if key_snapshot.exists:
                    raw_key = key_snapshot.to_dict() or {}
                    canonical_event_id = str(raw_key.get("event_id", ""))
                    if not canonical_event_id:
                        raise ValueError("Firestore observation key is missing event_id")

            event_ref = self._collection(self._events).document(canonical_event_id)
            event_snapshot = event_ref.get(transaction=transaction)
            if event_snapshot.exists:
                canonical_event = ProjectEvent.model_validate(self._payload(event_snapshot))
                if not self._same_observation(canonical_event, event):
                    raise ObservationRejectedError(
                        "source event id is already registered with different observation data"
                    )
                if canonical_event.source_event_id is None and event.source_event_id is not None:
                    canonical_event = canonical_event.model_copy(
                        update={"source_event_id": event.source_event_id}
                    )
            else:
                canonical_event = event.model_copy(update={"id": canonical_event_id})

            processing_ref = self._collection(self._event_processing).document(canonical_event.id)
            processing_snapshot = processing_ref.get(transaction=transaction)
            processing = processing_snapshot.to_dict() or {} if processing_snapshot.exists else {}
            state = str(processing.get("state", ""))
            completed_run_id = ""
            completed_run_snapshot = None
            if state == "SUCCESS":
                completed_run_id = str(processing.get("run_id", ""))
                completed_run_snapshot = self._collection(self._agent_runs).document(completed_run_id).get(
                    transaction=transaction
                )

            # Firestore transactions require all reads to happen before the first
            # write. Queue every event/key/claim write only after project, key,
            # canonical event, processing state, and any replay AgentRun are read.
            if not event_snapshot.exists or (
                ProjectEvent.model_validate(self._payload(event_snapshot)).source_event_id is None
                and canonical_event.source_event_id is not None
            ):
                transaction.set(
                    event_ref,
                    {"project_id": event.project_id, "data": canonical_event.model_dump(mode="json")},
                )
            if key_ref is not None and key_snapshot is not None and not key_snapshot.exists:
                transaction.set(
                    key_ref,
                    {"project_id": event.project_id, "event_id": canonical_event.id},
                )

            if state == "SUCCESS":
                if completed_run_snapshot is None:
                    raise RuntimeError("completed observation is missing its AgentRun")
                result = AgentRunResult.model_validate(self._payload(completed_run_snapshot))
                return ObservationClaim(
                    state=ObservationClaimState.REPLAY,
                    event=canonical_event,
                    run_id=completed_run_id,
                    existing_result=result,
                )

            if (
                state == "PROCESSING"
                and not self._claim_is_stale(str(processing.get("updated_at", "")))
            ):
                return ObservationClaim(
                    state=ObservationClaimState.IN_PROGRESS,
                    event=canonical_event,
                    run_id=str(processing.get("run_id", "")),
                )

            transaction.set(
                processing_ref,
                {
                    "project_id": canonical_event.project_id,
                    "run_id": run_id,
                    "state": "PROCESSING",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return ObservationClaim(
                state=ObservationClaimState.CLAIMED,
                event=canonical_event,
                run_id=run_id,
            )

        return self._transaction_runner(transaction, commit)

    def _assert_processing_claim(self, transaction: Any, event_id: str, run_id: str) -> Any:
        ref = self._collection(self._event_processing).document(event_id)
        snapshot = ref.get(transaction=transaction)
        raw = snapshot.to_dict() or {} if snapshot.exists else {}
        if raw.get("state") != "PROCESSING" or raw.get("run_id") != run_id:
            raise ValueError("observation processing claim changed before commit")
        return ref

    def commit_observation_result(
        self,
        *,
        event: ProjectEvent,
        run_id: str,
        plan: ObservationMutationPlan,
        result: AgentRunResult,
    ) -> None:
        if result.result != "SUCCESS" or result.agent_run_id != run_id or result.event_id != event.id:
            raise ValueError("successful observation commit requires the claimed successful AgentRun")
        write_count = (
            2
            + len(plan.tasks)
            + len(plan.proposals)
            + len(plan.notes)
            + int(plan.project is not None)
            + int(plan.architecture is not None)
        )
        if write_count > 500:
            raise ValueError(
                f"observation commit requires {write_count} Firestore writes; maximum is 500"
            )

        transaction = self.client.transaction()

        def commit(transaction: Any) -> None:
            processing_ref = self._assert_processing_claim(transaction, event.id, run_id)

            if plan.expected_project_updated_at is not None:
                project_snapshot = self._collection(self._projects).document(event.project_id).get(
                    transaction=transaction
                )
                if not project_snapshot.exists:
                    raise KeyError(event.project_id)
                current_project = Project.model_validate(self._payload(project_snapshot))
                if current_project.updated_at.isoformat() != plan.expected_project_updated_at:
                    raise ValueError("observation project state changed before commit")

            if plan.expected_architecture_version is not None:
                architecture_snapshot = self._collection(self._architectures).document(event.project_id).get(
                    transaction=transaction
                )
                current_architecture = (
                    Architecture.model_validate(self._payload(architecture_snapshot))
                    if architecture_snapshot.exists
                    else Architecture()
                )
                if current_architecture.version != plan.expected_architecture_version:
                    raise ValueError("observation architecture state changed before commit")

            for task_id, expected_updated_at in plan.expected_task_updated_at.items():
                task_snapshot = self._collection(self._tasks).document(task_id).get(transaction=transaction)
                raw_task = task_snapshot.to_dict() or {} if task_snapshot.exists else {}
                if not task_snapshot.exists or raw_task.get("project_id") != event.project_id:
                    raise ValueError("observation task state changed before commit")
                current_task = Task.model_validate(self._payload(task_snapshot))
                if current_task.updated_at.isoformat() != expected_updated_at:
                    raise ValueError("observation task state changed before commit")

            for proposal in plan.proposals:
                if proposal.project_id != event.project_id:
                    raise ValueError("proposal project_id mismatch during observation commit")
                for evidence_event_id in proposal.evidence_event_ids:
                    evidence_snapshot = self._collection(self._events).document(evidence_event_id).get(
                        transaction=transaction
                    )
                    evidence = ProjectEvent.model_validate(self._payload(evidence_snapshot))
                    if evidence.project_id != event.project_id:
                        raise ValueError("proposal evidence must reference an event from the same project")

            if plan.architecture is not None:
                transaction.set(
                    self._collection(self._architectures).document(event.project_id),
                    {"project_id": event.project_id, "data": plan.architecture.model_dump(mode="json")},
                )
            if plan.project is not None:
                if plan.project.id != event.project_id:
                    raise ValueError("project mutation does not match observation project")
                transaction.set(
                    self._collection(self._projects).document(plan.project.id),
                    {"data": plan.project.model_dump(mode="json")},
                )
            for task in plan.tasks:
                transaction.set(
                    self._collection(self._tasks).document(task.id),
                    {"project_id": event.project_id, "data": task.model_dump(mode="json")},
                )
            for proposal in plan.proposals:
                transaction.set(
                    self._collection(self._proposals).document(proposal.id),
                    {"project_id": proposal.project_id, "data": proposal.model_dump(mode="json")},
                )
            for index, note in enumerate(plan.notes):
                note_id = sha256(f"{run_id}|{index}".encode("utf-8")).hexdigest()
                transaction.set(
                    self._collection(self._notes).document(note_id),
                    {
                        "project_id": event.project_id,
                        "note": note,
                        "created_at": result.completed_at.isoformat(),
                    },
                )

            transaction.set(
                self._collection(self._agent_runs).document(result.agent_run_id),
                {
                    "project_id": event.project_id,
                    "event_id": event.id,
                    "data": result.model_dump(mode="json"),
                },
            )
            transaction.set(
                processing_ref,
                {
                    "project_id": event.project_id,
                    "run_id": run_id,
                    "state": "SUCCESS",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        self._transaction_runner(transaction, commit)

    def fail_observation(
        self,
        *,
        event: ProjectEvent,
        run_id: str,
        result: AgentRunResult,
    ) -> None:
        if result.result != "ERROR" or result.agent_run_id != run_id or result.event_id != event.id:
            raise ValueError("failed observation commit requires the claimed failed AgentRun")
        transaction = self.client.transaction()

        def commit(transaction: Any) -> None:
            processing_ref = self._assert_processing_claim(transaction, event.id, run_id)
            transaction.set(
                self._collection(self._agent_runs).document(result.agent_run_id),
                {
                    "project_id": event.project_id,
                    "event_id": event.id,
                    "data": result.model_dump(mode="json"),
                },
            )
            transaction.set(
                processing_ref,
                {
                    "project_id": event.project_id,
                    "run_id": run_id,
                    "state": "FAILED",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        self._transaction_runner(transaction, commit)

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
