import pytest
from fastapi.testclient import TestClient

import archbro.integrations.firebase as firebase_integration
from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import create_app
from conftest import requires_database

pytestmark = requires_database


def _repository(dsn) -> PostgresProjectRepository:
    return PostgresProjectRepository(dsn)


def test_firebase_auth_mode_constructs_canonical_provider_and_forwards_token(dsn,
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

    client = TestClient(create_app(_repository(dsn), FakeModelProvider()))

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


def test_firebase_auth_mode_requires_a_google_project(dsn,
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
        create_app(_repository(dsn), FakeModelProvider())


def _production_google_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        firebase_integration,
        "FirebasePrincipalProvider",
        lambda project_id: (lambda token: None),
    )
    monkeypatch.setenv("ARCHBRO_ENV", "production")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "archbro-test-project")
    monkeypatch.setenv("ARCHBRO_FIREBASE_API_KEY", "example-key")
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "off")


def test_production_google_sign_in_requires_the_browser_auth_domain(
    dsn,
    monkeypatch: pytest.MonkeyPatch,
):
    # Google sign-in opens https://<authDomain>/__/auth/handler. With no
    # authDomain the pinned SDK raises auth/auth-domain-config-required at the
    # moment a person clicks the button, so a forgotten deployment setting
    # would reach real users as a broken button on a server that booted
    # cleanly. Refuse to start instead.
    _production_google_environment(monkeypatch)
    monkeypatch.delenv("ARCHBRO_FIREBASE_AUTH_DOMAIN", raising=False)

    with pytest.raises(ValueError) as error:
        create_app(_repository(dsn), FakeModelProvider())

    assert "authDomain" in str(error.value)


def test_production_boots_once_the_browser_auth_domain_is_present(
    dsn,
    monkeypatch: pytest.MonkeyPatch,
):
    _production_google_environment(monkeypatch)
    monkeypatch.setenv("ARCHBRO_FIREBASE_AUTH_DOMAIN", "archbro-test.firebaseapp.com")

    app = create_app(_repository(dsn), FakeModelProvider())

    with TestClient(app) as client:
        config = client.get("/runtime-config.js")
    assert "archbro-test.firebaseapp.com" in config.text
