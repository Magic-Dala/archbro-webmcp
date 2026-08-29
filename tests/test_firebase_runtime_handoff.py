from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient

import archbro.integrations.firebase as firebase_integration
from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.repository import ProjectRepository
from archbro.platform.runtime.app import create_app


def _repository() -> ProjectRepository:
    database = Path(tempfile.mkdtemp()) / "firebase-runtime-handoff.db"
    return ProjectRepository(str(database))


def test_firebase_auth_mode_constructs_canonical_provider_and_forwards_token(
    monkeypatch: pytest.MonkeyPatch,
):
    constructed_projects: list[str] = []
    received_tokens: list[str] = []

    def fake_provider_type(project_id: str):
        constructed_projects.append(project_id)

        async def provide(token: str) -> TrustedPrincipal:
            received_tokens.append(token)
            return TrustedPrincipal(
                user_id="firebase-uid-alice",
                team_ids=[],
                local_development=False,
            )

        return provide

    monkeypatch.setattr(
        firebase_integration,
        "FirebasePrincipalProvider",
        fake_provider_type,
    )
    monkeypatch.setenv("ARCHBRO_ENV", "test")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "archbro-test-project")
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "off")

    client = TestClient(create_app(_repository(), FakeModelProvider()))

    missing_credentials = client.get("/projects")
    authenticated = client.get(
        "/projects",
        headers={"Authorization": "Bearer safe-test-token"},
    )

    assert constructed_projects == ["archbro-test-project"]
    assert received_tokens == ["safe-test-token"]
    assert missing_credentials.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json() == []


def test_firebase_auth_mode_requires_a_google_project(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ARCHBRO_ENV", "test")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "off")
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(
        ValueError,
        match="FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required",
    ):
        create_app(_repository(), FakeModelProvider())
