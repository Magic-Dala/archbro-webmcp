from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.contracts import (
    AgentRunResult,
    Architecture,
    ObservationClaimState,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    Project,
    ProjectEvent,
    ProjectEventType,
    ProposalStatus,
    Task,
    TaskStatus,
)
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.observation import ObservationMutationPlan
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.firestore import FirestoreProjectRepository
from archbro.platform.runtime.app import create_app


class _Snapshot:
    def __init__(self, collection: "_Collection", doc_id: str) -> None:
        self._collection = collection
        self.id = doc_id
        self.reference = _DocumentRef(collection, doc_id)

    @property
    def exists(self) -> bool:
        return self.id in self._collection.docs

    def to_dict(self):
        value = self._collection.docs.get(self.id)
        return deepcopy(value) if value is not None else None


class _DocumentRef:
    def __init__(self, collection: "_Collection", doc_id: str) -> None:
        self._collection = collection
        self.id = doc_id

    def set(self, value) -> None:
        self._collection.docs[self.id] = deepcopy(value)

    def get(self, transaction=None) -> _Snapshot:
        if transaction is not None and not transaction.in_progress:
            raise ValueError("Transaction not in progress, cannot be used in API requests.")
        if transaction is not None and hasattr(transaction, "record_read"):
            transaction.record_read()
        return _Snapshot(self._collection, self.id)

    def delete(self) -> None:
        self._collection.docs.pop(self.id, None)


class _Query:
    def __init__(self, collection: "_Collection", field: str, value: object) -> None:
        self._collection = collection
        self._field = field
        self._value = value
        self._order_field: str | None = None
        self._descending = False
        self._limit: int | None = None

    @staticmethod
    def _field_value(payload: dict, field_path: str):
        value = payload
        for part in field_path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def order_by(self, field: str, direction=None):
        self._order_field = field
        self._descending = str(direction).upper().endswith("DESCENDING")
        return self

    def limit(self, value: int):
        self._limit = value
        self._collection.last_query_limit = value
        return self

    def stream(self):
        snapshots = [
            _Snapshot(self._collection, doc_id)
            for doc_id, payload in self._collection.docs.items()
            if payload.get(self._field) == self._value
        ]
        if self._order_field is not None:
            snapshots.sort(
                key=lambda snapshot: self._field_value(
                    self._collection.docs[snapshot.id], self._order_field
                ),
                reverse=self._descending,
            )
        if self._limit is not None:
            snapshots = snapshots[: self._limit]
        return snapshots


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self._counter = 0
        self.last_query_limit: int | None = None

    def document(self, doc_id: str) -> _DocumentRef:
        return _DocumentRef(self, doc_id)

    def stream(self):
        return [_Snapshot(self, doc_id) for doc_id in self.docs]

    def add(self, value):
        self._counter += 1
        doc_id = f"auto_{self._counter}"
        ref = self.document(doc_id)
        ref.set(value)
        return None, ref

    def where(self, *args, **kwargs):
        if "filter" in kwargs:
            filter_value = kwargs["filter"]
            field = getattr(filter_value, "field_path")
            value = getattr(filter_value, "value")
        else:
            field, _operator, value = args
        return _Query(self, field, value)


class _Transaction:
    def __init__(self, client: "_FakeFirestoreClient") -> None:
        self.client = client
        self.operations: list[tuple[_DocumentRef, dict]] = []
        self.in_progress = False

    def record_read(self) -> None:
        if self.operations:
            raise RuntimeError("Firestore transaction read after write")

    def set(self, ref: _DocumentRef, value: dict) -> None:
        self.operations.append((ref, deepcopy(value)))

    def commit(self) -> None:
        if not self.in_progress:
            raise ValueError("Transaction not in progress")
        if self.client.fail_next_transaction:
            self.client.fail_next_transaction = False
            raise RuntimeError("injected Firestore transaction failure")
        for ref, value in self.operations:
            ref.set(value)
        self.client.transaction_commits += 1


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}
        self.transaction_commits = 0
        self.fail_next_transaction = False

    def collection(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())

    def transaction(self):
        return _Transaction(self)


def _fake_transaction_runner(transaction: _Transaction, operation):
    transaction.in_progress = True
    try:
        result = operation(transaction)
        transaction.commit()
        return result
    finally:
        transaction.in_progress = False

def _repo(client: _FakeFirestoreClient, *, collection_prefix: str) -> FirestoreProjectRepository:
    return FirestoreProjectRepository(
        client,
        collection_prefix=collection_prefix,
        transaction_runner=_fake_transaction_runner,
    )


def test_firestore_repository_implements_archbro_project_state_contract():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="qa")

    project = Project(name="Firestore QA", goal="Keep project state durable")
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1, summary="V1"))
    task = Task(title="Persist task")
    repo.save_task(project.id, task)
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        reason="Check review persistence",
        evidence=["QA evidence"],
        observed_change="QA change",
        impact="No production impact",
        recommended_option=ArchitectureOption.KEEP_CURRENT,
    )
    repo.save_proposal(proposal)
    repo.save_event(ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE))
    repo.add_note(project.id, "first note")

    assert repo.get_project(project.id).name == "Firestore QA"
    assert repo.list_projects()[0].id == project.id
    assert repo.get_architecture(project.id).version == 1
    assert repo.list_tasks(project.id)[0].id == task.id
    assert repo.get_proposal(proposal.id).id == proposal.id
    context = repo.load_context(project.id)
    assert context.project.id == project.id
    assert context.recent_notes == ["first note"]
    assert [item.id for item in context.pending_proposals] == [proposal.id]

    assert repo.delete_project(project.id) is True
    assert repo.delete_project(project.id) is False
    with pytest.raises(KeyError):
        repo.get_project(project.id)


def test_firestore_event_action_commit_is_atomic_on_failure():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="atomic")
    project = Project(name="Atomic", goal="Keep event and mutations together")
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))
    task = Task(title="Original task")
    repo.save_task(project.id, task)

    event = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE)
    changed_task = task.model_copy(update={"title": "Changed task"})
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        reason="Atomic test",
        evidence=["test evidence"],
        observed_change="test change",
        impact="test impact",
        recommended_option=ArchitectureOption.KEEP_CURRENT,
    )

    client.fail_next_transaction = True
    with pytest.raises(RuntimeError, match="injected Firestore transaction failure"):
        repo.commit_event_actions(
            event=event,
            project=None,
            architecture=None,
            tasks=[changed_task],
            proposals=[proposal],
            notes=["atomic note"],
        )

    assert repo.get_task(task.id).title == "Original task"
    with pytest.raises(KeyError):
        repo.get_proposal(proposal.id)
    assert event.id not in client.collection("atomic_events").docs
    assert client.collection("atomic_notes").docs == {}
    assert client.transaction_commits == 0


def test_runtime_selects_firestore_when_configured(monkeypatch):
    client = _FakeFirestoreClient()
    from archbro.integrations.firebase import admin as firebase_admin

    calls: list[tuple[str, str]] = []

    def fake_client(project_id: str, database_id: str):
        calls.append((project_id, database_id))
        return client

    monkeypatch.setattr(firebase_admin, "get_firestore_client", fake_client)
    monkeypatch.setenv("ARCHBRO_PERSISTENCE", "firestore")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "kbf-derived-project")
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", "(default)")
    monkeypatch.setenv("ARCHBRO_FIRESTORE_PREFIX", "archbro_test")

    app = create_app(provider=FakeModelProvider())
    http = TestClient(app)
    response = http.post(
        "/projects",
        json={"name": "Cloud Project", "goal": "Use Firestore", "description": ""},
    )
    assert response.status_code == 200
    project_id = response.json()["id"]
    assert http.get(f"/projects/{project_id}").status_code == 200
    assert calls == [("kbf-derived-project", "(default)")]
    assert "archbro_test_projects" in client.collections


def test_runtime_rejects_firestore_without_google_project(monkeypatch):
    monkeypatch.setenv("ARCHBRO_PERSISTENCE", "firestore")
    monkeypatch.delenv("FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(ValueError, match="FIRESTORE_PROJECT_ID"):
        create_app(provider=FakeModelProvider())


def test_firestore_acceptance_commits_architecture_project_tasks_and_proposal_in_one_transaction():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="m5")
    project = Project(name="Firestore M5", goal="Reconcile acceptance", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist project state.",
                )
            ],
        ),
    )
    task = Task(title="Prepare PostgreSQL persistence", related_component="database")
    repo.save_task(project.id, task)
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="Persistence changed",
        evidence=["Firestore selected"],
        observed_change="PostgreSQL to Firestore",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
            }
        ],
        impact="Persistence work changes",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert client.transaction_commits == 1
    assert repo.get_architecture(project.id).version == 2
    assert repo.get_project(project.id).architecture_version == 2
    assert repo.get_task(task.id).status == TaskStatus.BLOCKED
    assert repo.get_proposal(proposal.id).status.value == "ACCEPTED"


def test_firestore_acceptance_transaction_failure_leaves_all_previous_state_unchanged():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="m5fail")
    project = Project(name="Firestore M5", goal="Reconcile acceptance", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist project state.",
                )
            ],
        ),
    )
    task = Task(title="Prepare PostgreSQL persistence", related_component="database")
    repo.save_task(project.id, task)
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="Persistence changed",
        evidence=["Firestore selected"],
        observed_change="PostgreSQL to Firestore",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
            }
        ],
        impact="Persistence work changes",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    repo.save_proposal(proposal)
    client.fail_next_transaction = True

    with pytest.raises(RuntimeError, match="injected Firestore transaction failure"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == 1
    assert repo.get_project(project.id).architecture_version == 1
    assert repo.get_task(task.id).status == TaskStatus.TODO
    assert repo.get_proposal(proposal.id).status.value == "PENDING"


def test_firestore_acceptance_rechecks_base_version_inside_transaction(monkeypatch):
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="m5race")
    project = Project(name="Firestore Race", goal="Serialize approval", architecture_version=1)
    repo.save_project(project)
    initial_architecture = Architecture(
        version=1,
        components=[
            Component(
                id="database",
                name="PostgreSQL",
                type="database",
                responsibility="Persist project state.",
            )
        ],
    )
    repo.save_architecture(project.id, initial_architecture)

    def proposal(new_name: str) -> ArchitectureChangeProposal:
        return ArchitectureChangeProposal(
            project_id=project.id,
            base_architecture_version=1,
            reason="Persistence changed",
            evidence=[f"{new_name} selected"],
            observed_change=f"PostgreSQL to {new_name}",
            affected_components=["database"],
            proposed_changes=[
                {
                    "operation": "replace_component",
                    "component_id": "database",
                    "new_name": new_name,
                }
            ],
            impact="Persistence work changes",
            recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
        )

    first = proposal("Firestore")
    second = proposal("Spanner")
    repo.save_proposal(first)
    repo.save_proposal(second)
    ActionExecutor(repo).accept_proposal(project.id, first.id)

    monkeypatch.setattr(repo, "get_architecture", lambda _project_id: initial_architecture)
    monkeypatch.setattr(repo, "get_project", lambda _project_id: project)

    with pytest.raises(ValueError, match="accepted architecture changed before proposal commit"):
        ActionExecutor(repo).accept_proposal(project.id, second.id)

    accepted = FirestoreProjectRepository.get_architecture(repo, project.id)
    assert accepted.version == 2
    assert accepted.find_component("database").name == "Firestore"
    assert FirestoreProjectRepository.get_proposal(repo, second.id).status.value == "PENDING"


def test_firestore_reject_cannot_overwrite_a_concurrent_accept(monkeypatch):
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="m5decision")
    project = Project(name="Firestore Decision", goal="Serialize review", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist project state.",
                )
            ],
        ),
    )
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="Persistence changed",
        evidence=["Firestore selected"],
        observed_change="PostgreSQL to Firestore",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
            }
        ],
        impact="Persistence work changes",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    repo.save_proposal(proposal)
    stale_pending = repo.get_proposal(proposal.id)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)
    assert FirestoreProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.ACCEPTED

    monkeypatch.setattr(repo, "get_proposal", lambda _proposal_id: stale_pending)
    with pytest.raises(ValueError, match="proposal status changed before decision commit"):
        ActionExecutor(repo).reject_proposal(project.id, proposal.id)

    assert FirestoreProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.ACCEPTED


def test_firestore_acceptance_rejects_concurrent_task_update(monkeypatch):
    from datetime import timedelta

    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="m5taskrace")
    project = Project(
        name="Firestore Task Race",
        goal="Protect human task state",
        architecture_version=1,
    )
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist project state.",
                )
            ],
        ),
    )
    task = Task(
        title="Validate persistence recovery",
        status=TaskStatus.IN_PROGRESS,
        related_component="database",
    )
    repo.save_task(project.id, task)
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="Persistence changed",
        evidence=["Firestore selected"],
        observed_change="PostgreSQL to Firestore",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
            }
        ],
        impact="Persistence work changes",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    repo.save_proposal(proposal)
    stale_tasks = repo.list_tasks(project.id)

    original_save = repo.save_acceptance_state

    def concurrent_save(**kwargs):
        current = repo.get_task(task.id)
        repo.save_task(
            project.id,
            current.model_copy(
                update={
                    "status": TaskStatus.DONE,
                    "updated_at": current.updated_at + timedelta(seconds=1),
                }
            ),
        )
        return original_save(**kwargs)

    monkeypatch.setattr(repo, "list_tasks", lambda _project_id: stale_tasks)
    monkeypatch.setattr(repo, "save_acceptance_state", concurrent_save)

    with pytest.raises(ValueError, match="acceptance task changed before proposal commit"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert FirestoreProjectRepository.get_task(repo, task.id).status == TaskStatus.DONE
    assert FirestoreProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.PENDING
    assert FirestoreProjectRepository.get_architecture(repo, project.id).version == 1


def test_firestore_observation_source_key_replays_one_durable_run():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="trace")
    project = Project(name="Trace", goal="Trace observations", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-42",
        payload={"message": "internal refactor"},
    )

    claim = repo.claim_observation(event, run_id="run_trace")
    assert claim.state == ObservationClaimState.CLAIMED
    result = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id=claim.run_id,
        summary="aligned",
        actions=[],
        architecture_review_required=False,
        provider="fake",
        model="fake",
        result="SUCCESS",
    )
    repo.commit_observation_result(
        event=claim.event,
        run_id=claim.run_id,
        plan=ObservationMutationPlan(),
        result=result,
    )

    replay = repo.claim_observation(
        event.model_copy(update={"id": "event_second_delivery"}),
        run_id="run_should_not_execute",
    )
    assert replay.state == ObservationClaimState.REPLAY
    assert replay.event.id == claim.event.id
    assert replay.existing_result is not None
    assert replay.existing_result.agent_run_id == "run_trace"
    assert len(repo.list_events(project.id)) == 1
    assert len(repo.list_agent_runs(project.id)) == 1


def test_firestore_failed_observation_can_be_reclaimed_without_duplicate_event():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="retry")
    project = Project(name="Retry", goal="Retry observations", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-retry",
        payload={"message": "retry me"},
    )
    claim = repo.claim_observation(event, run_id="run_failed")
    failed = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id=claim.run_id,
        summary="failed",
        actions=[],
        architecture_review_required=False,
        provider="fake",
        model="fake",
        result="ERROR",
        error="transient",
    )
    repo.fail_observation(event=claim.event, run_id=claim.run_id, result=failed)

    retry = repo.claim_observation(
        event.model_copy(update={"id": "event_retry_request"}),
        run_id="run_retry",
    )
    assert retry.state == ObservationClaimState.CLAIMED
    assert retry.event.id == claim.event.id
    assert retry.run_id == "run_retry"
    assert len(repo.list_events(project.id)) == 1
    assert [run.result for run in repo.list_agent_runs(project.id)] == ["ERROR"]


def test_firestore_observation_transaction_failure_leaves_effect_unapplied():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="tracefail")
    project = Project(name="Trace Fail", goal="Atomic observations", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist state",
                )
            ],
        ),
    )
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-atomic",
        payload={"message": "database changed"},
    )
    claim = repo.claim_observation(event, run_id="run_atomic")
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="database changed",
        evidence=["observed change"],
        evidence_event_ids=[claim.event.id],
        observed_change="PostgreSQL to Firestore",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
            }
        ],
        impact="persistence work",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    result = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id=claim.run_id,
        summary="proposal",
        actions=[],
        architecture_review_required=True,
        proposal_ids=[proposal.id],
        provider="fake",
        model="fake",
        result="SUCCESS",
    )
    client.fail_next_transaction = True

    with pytest.raises(RuntimeError, match="injected Firestore transaction failure"):
        repo.commit_observation_result(
            event=claim.event,
            run_id=claim.run_id,
            plan=ObservationMutationPlan(proposals=[proposal]),
            result=result,
        )

    assert repo.list_proposals(project.id) == []
    assert repo.list_agent_runs(project.id) == []
    assert repo.get_architecture(project.id).version == 1

    failed = result.model_copy(
        update={
            "result": "ERROR",
            "summary": "transaction failed",
            "proposal_ids": [],
            "error": "injected Firestore transaction failure",
        }
    )
    repo.fail_observation(event=claim.event, run_id=claim.run_id, result=failed)
    retry = repo.claim_observation(
        event.model_copy(update={"id": "event_atomic_retry"}),
        run_id="run_after_failure",
    )
    assert retry.state == ObservationClaimState.CLAIMED
    assert retry.event.id == claim.event.id


def test_firestore_activity_history_queries_are_bounded_before_streaming():
    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="bounded")
    project = Project(name="Bounded", goal="Bound activity history", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))

    for index in range(25):
        event = ProjectEvent(
            project_id=project.id,
            type=ProjectEventType.GITHUB_CHANGE,
            source="GITHUB",
            source_event_id=f"delivery-{index}",
            payload={"message": f"change-{index}"},
        )
        repo.save_event(event)
        run = AgentRunResult(
            project_id=project.id,
            event_id=event.id,
            agent_run_id=f"run_{index}",
            summary=f"run-{index}",
            actions=[],
            architecture_review_required=False,
            provider="fake",
            model="fake",
            result="SUCCESS",
        )
        repo._collection(repo._agent_runs).document(run.agent_run_id).set(
            {
                "project_id": project.id,
                "event_id": event.id,
                "data": run.model_dump(mode="json"),
            }
        )

    events = repo.list_events(project.id, limit=5)
    assert len(events) == 5
    assert client.collection(repo._events).last_query_limit == 5

    runs = repo.list_agent_runs(project.id, limit=4)
    assert len(runs) == 4
    assert client.collection(repo._agent_runs).last_query_limit == 4


def test_firestore_observation_commit_rejects_stale_task_plan():
    from datetime import timedelta

    client = _FakeFirestoreClient()
    repo = _repo(client, collection_prefix="observationrace")
    project = Project(name="Observation Race", goal="Protect task state", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))
    task = Task(title="Human task", status=TaskStatus.TODO)
    repo.save_task(project.id, task)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        source="HUMAN",
        source_event_id="firestore-stale-observation",
        payload={"message": "Update the task."},
    )
    claim = repo.claim_observation(event, run_id="run_firestore_stale")
    stale_task = task.model_copy(
        update={
            "status": TaskStatus.IN_PROGRESS,
            "updated_at": task.updated_at + timedelta(milliseconds=1),
        }
    )
    plan = ObservationMutationPlan(
        tasks=[stale_task],
        expected_task_updated_at={task.id: task.updated_at.isoformat()},
    )
    current = repo.get_task(task.id)
    repo.save_task(
        project.id,
        current.model_copy(
            update={
                "status": TaskStatus.DONE,
                "updated_at": current.updated_at + timedelta(seconds=1),
            }
        ),
    )
    result = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id=claim.run_id,
        summary="stale task plan",
        actions=[],
        architecture_review_required=False,
        provider="test",
        model="test",
        result="SUCCESS",
    )

    with pytest.raises(ValueError, match="observation task state changed before commit"):
        repo.commit_observation_result(
            event=claim.event,
            run_id=claim.run_id,
            plan=plan,
            result=result,
        )

    assert repo.get_task(task.id).status == TaskStatus.DONE
    assert repo.list_agent_runs(project.id) == []
