from typing import cast

import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.runtime.app import create_app


def _repository_not_used_by_runtime_config() -> ProjectRepositoryPort:
    return cast(ProjectRepositoryPort, object())


def test_firebase_popup_csp_includes_the_validated_custom_auth_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHBRO_ENV", "test")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "archbro-test-project")
    monkeypatch.setenv("ARCHBRO_FIREBASE_API_KEY", "test-browser-key")
    monkeypatch.setenv(
        "ARCHBRO_FIREBASE_AUTH_DOMAIN",
        "login.example.archbro.invalid",
    )

    async def principal_provider(_: str) -> TrustedPrincipal:
        return TrustedPrincipal(user_id="firebase-uid-alice")

    client = TestClient(
        create_app(
            _repository_not_used_by_runtime_config(),
            FakeModelProvider(),
            principal_provider=principal_provider,
        )
    )
    policy = client.get("/runtime-config.js").headers["content-security-policy"]

    assert "script-src 'self' https://www.gstatic.com https://apis.google.com" in policy
    assert (
        "frame-src https://*.firebaseapp.com "
        "https://login.example.archbro.invalid"
    ) in policy


@pytest.mark.parametrize(
    "invalid_auth_domain",
    [
        "https://project.firebaseapp.com",
        "project.firebaseapp.com/path",
        "project.firebaseapp.com:443",
        "project.firebaseapp.com; script-src *",
    ],
)
def test_firebase_popup_csp_rejects_unsafe_auth_domains(
    monkeypatch: pytest.MonkeyPatch,
    invalid_auth_domain: str,
) -> None:
    monkeypatch.setenv("ARCHBRO_ENV", "test")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "archbro-test-project")
    monkeypatch.setenv("ARCHBRO_FIREBASE_API_KEY", "test-browser-key")
    monkeypatch.setenv("ARCHBRO_FIREBASE_AUTH_DOMAIN", invalid_auth_domain)

    async def principal_provider(_: str) -> TrustedPrincipal:
        return TrustedPrincipal(user_id="firebase-uid-alice")

    with pytest.raises(ValueError, match="must be a hostname"):
        create_app(
            _repository_not_used_by_runtime_config(),
            FakeModelProvider(),
            principal_provider=principal_provider,
        )


def test_local_mode_does_not_trust_popup_only_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHBRO_ENV", "test")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "local")

    client = TestClient(
        create_app(
            _repository_not_used_by_runtime_config(),
            FakeModelProvider(),
        )
    )
    policy = client.get("/runtime-config.js").headers["content-security-policy"]

    assert "https://apis.google.com" not in policy
    assert "frame-src 'none'" in policy
