from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Project,
    ProjectEvent,
    ProjectEventType,
    Task,
)
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

    def get(self) -> _Snapshot:
        return _Snapshot(self._collection, self.id)

    def delete(self) -> None:
        self._collection.docs.pop(self.id, None)


class _Query:
    def __init__(self, collection: "_Collection", field: str, value: object) -> None:
        self._collection = collection
        self._field = field
        self._value = value

    def stream(self):
        return [
            _Snapshot(self._collection, doc_id)
            for doc_id, payload in self._collection.docs.items()
            if payload.get(self._field) == self._value
        ]


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self._counter = 0

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


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def collection(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


def test_firestore_repository_implements_archbro_project_state_contract():
    client = _FakeFirestoreClient()
    repo = FirestoreProjectRepository(client, collection_prefix="qa")

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
