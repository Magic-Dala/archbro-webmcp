import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.authorization import (
    IdentityProviderUnavailableError,
    InvalidCredentialsError,
    ProjectAuthorizationError,
    ProjectAuthorizer,
    ProjectPermission,
    TrustedPrincipal,
)
from archbro.backend.core.contracts import Architecture, Component, Project
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import build_app
from conftest import requires_database

pytestmark = requires_database


def _client(
    repo: PostgresProjectRepository,
    principal: TrustedPrincipal,
    *,
    token: str = "test-firebase-token",
) -> TestClient:
    async def principal_provider(received_token: str) -> TrustedPrincipal:
        assert received_token == token
        return principal

    client = TestClient(
        build_app(
            repo,
            FakeModelProvider(),
            principal_provider=principal_provider,
        )
    )
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _repo(dsn) -> PostgresProjectRepository:
    return PostgresProjectRepository(dsn)


def _create_project(client: TestClient, *, team_id: str | None = None) -> dict:
    body = {
        "name": "Archbro",
        "goal": "Keep a living architecture aligned with project activity.",
        "description": "Authorization test project",
    }
    if team_id is not None:
        body["team_id"] = team_id
    response = client.post("/projects", json=body)
    assert response.status_code == 200
    return response.json()


def test_verified_firebase_uid_is_canonical_owner_identity(dsn):
    repo = _repo(dsn)
    firebase_uid = "firebase-uid-alice"
    alice = _client(repo, TrustedPrincipal(user_id=firebase_uid, team_ids=[]))

    project = _create_project(alice)

    assert project["owner_user_id"] == firebase_uid
    assert project["team_id"] is None


def test_backend_extracts_bearer_token_before_calling_async_provider(dsn):
    repo = _repo(dsn)
    received_tokens: list[str] = []

    async def principal_provider(token: str) -> TrustedPrincipal:
        received_tokens.append(token)
        return TrustedPrincipal(user_id="firebase-uid-alice")

    client = TestClient(build_app(repo, FakeModelProvider(), principal_provider=principal_provider))
    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer signed.firebase.token"},
        json={"name": "Archbro", "goal": "Verify the auth boundary."},
    )

    assert response.status_code == 200
    assert received_tokens == ["signed.firebase.token"]


def test_missing_or_malformed_bearer_token_returns_401_without_calling_provider(dsn):
    repo = _repo(dsn)
    calls = 0

    async def principal_provider(_: str) -> TrustedPrincipal:
        nonlocal calls
        calls += 1
        return TrustedPrincipal(user_id="should-not-run")

    client = TestClient(build_app(repo, FakeModelProvider(), principal_provider=principal_provider))

    missing = client.get("/projects")
    malformed = client.get("/projects", headers={"Authorization": "Basic abc"})
    empty = client.get("/projects", headers={"Authorization": "Bearer   "})

    for response in (missing, malformed, empty):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    assert calls == 0


def test_invalid_credentials_map_to_401_and_provider_unavailable_maps_to_503(dsn):
    repo = _repo(dsn)

    async def invalid_provider(_: str) -> TrustedPrincipal:
        raise InvalidCredentialsError("firebase token is invalid")

    async def unavailable_provider(_: str) -> TrustedPrincipal:
        raise IdentityProviderUnavailableError("firebase unavailable")

    invalid_client = TestClient(build_app(repo, FakeModelProvider(), principal_provider=invalid_provider))
    unavailable_client = TestClient(build_app(repo, FakeModelProvider(), principal_provider=unavailable_provider))
    headers = {"Authorization": "Bearer token"}

    invalid = invalid_client.get("/projects", headers=headers)
    unavailable = unavailable_client.get("/projects", headers=headers)

    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "firebase token is invalid"
    assert invalid.headers["www-authenticate"] == "Bearer"
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "firebase unavailable"


def test_owner_identity_is_server_trusted_and_other_user_is_denied(dsn):
    repo = _repo(dsn)
    alice = _client(repo, TrustedPrincipal(user_id="alice"))
    bob = _client(repo, TrustedPrincipal(user_id="bob"))

    project = _create_project(alice)
    project_id = project["id"]
    assert project["owner_user_id"] == "alice"

    assert alice.get(f"/projects/{project_id}").status_code == 200
    assert bob.get(f"/projects/{project_id}").status_code == 403
    assert bob.patch(f"/projects/{project_id}", json={"name": "Hijacked"}).status_code == 403
    assert bob.delete(f"/projects/{project_id}").status_code == 403
    assert bob.get("/projects").json() == []

    spoofed = bob.get(f"/projects/{project_id}", headers={"X-User-ID": "alice"})
    assert spoofed.status_code == 403


def test_node_context_and_path_preserve_project_read_authorization(dsn):
    repo = _repo(dsn)
    alice = _client(repo, TrustedPrincipal(user_id="alice"))
    bob = _client(repo, TrustedPrincipal(user_id="bob"))
    project = _create_project(alice)
    project_id = project["id"]
    repo.save_architecture(
        project_id,
        Architecture(
            version=1,
            components=[
                Component(id="a", name="A", type="service", responsibility="A"),
                Component(id="b", name="B", type="service", responsibility="B"),
            ],
        ),
    )

    assert alice.get(f"/projects/{project_id}/architecture/nodes/node:a/context").status_code == 200
    assert bob.get(f"/projects/{project_id}/architecture/nodes/node:a/context").status_code == 403
    assert bob.get(
        f"/projects/{project_id}/architecture/path",
        params={"source_id": "node:a", "target_id": "node:b"},
    ).status_code == 403


def test_trusted_team_member_can_work_and_review_but_not_manage_project(dsn):
    repo = _repo(dsn)
    alice_principal = TrustedPrincipal(user_id="alice", team_ids=["team-1"])
    bob_principal = TrustedPrincipal(user_id="bob", team_ids=["team-1"])
    alice = _client(repo, alice_principal)
    bob = _client(repo, bob_principal)

    project = _create_project(alice, team_id="team-1")
    project_id = project["id"]

    assert bob.get(f"/projects/{project_id}").status_code == 200
    assert [item["id"] for item in bob.get("/projects").json()] == [project_id]

    edited = bob.patch(
        f"/projects/{project_id}",
        json={"description": "Updated by trusted team member"},
    )
    assert edited.status_code == 200
    assert edited.json()["description"] == "Updated by trusted team member"

    authorizer = ProjectAuthorizer()
    stored_project = repo.get_project(project_id)
    authorizer.require(bob_principal, stored_project, ProjectPermission.REVIEW)
    with pytest.raises(ProjectAuthorizationError):
        authorizer.require(bob_principal, stored_project, ProjectPermission.MANAGE)

    # MANAGE is owner-only in the MVP policy.
    assert bob.delete(f"/projects/{project_id}").status_code == 403
    assert alice.delete(f"/projects/{project_id}").status_code == 204


def test_project_cannot_be_created_for_team_not_present_in_trusted_identity(dsn):
    repo = _repo(dsn)
    client = _client(repo, TrustedPrincipal(user_id="alice", team_ids=["team-1"]))

    response = client.post(
        "/projects",
        json={
            "name": "Wrong Team",
            "goal": "This must not cross a trusted team boundary.",
            "team_id": "team-2",
        },
    )
    assert response.status_code == 403
    assert repo.list_projects() == []


def test_real_trusted_identity_fails_closed_on_legacy_unowned_project(dsn):
    repo = _repo(dsn)
    legacy = Project(
        name="Legacy",
        goal="Pre-auth project without trusted ownership metadata.",
    )
    repo.save_project(legacy)
    repo.save_architecture(legacy.id, Architecture())

    trusted = _client(repo, TrustedPrincipal(user_id="alice"))
    assert trusted.get(f"/projects/{legacy.id}").status_code == 403
    assert trusted.get("/projects").json() == []

    # No configured auth provider keeps the explicit local-development path working.
    local = TestClient(build_app(repo, FakeModelProvider()))
    assert local.get(f"/projects/{legacy.id}").status_code == 200


def test_authentication_precedes_project_lookup_for_missing_project(dsn):
    repo = _repo(dsn)
    calls = 0

    async def unavailable_provider(_: str) -> TrustedPrincipal:
        nonlocal calls
        calls += 1
        raise IdentityProviderUnavailableError("firebase unavailable")

    client = TestClient(build_app(repo, FakeModelProvider(), principal_provider=unavailable_provider))

    missing_credentials = client.get("/projects/project_missing")
    assert missing_credentials.status_code == 401
    assert calls == 0

    unavailable = client.get(
        "/projects/project_missing",
        headers={"Authorization": "Bearer token"},
    )
    assert unavailable.status_code == 503
    assert calls == 1

    valid = _client(repo, TrustedPrincipal(user_id="alice"))
    assert valid.get("/projects/project_missing").status_code == 404


def test_goal_drafting_requires_trusted_identity_before_provider_call(dsn):
    repo = _repo(dsn)
    calls = 0

    class CountingProvider(FakeModelProvider):
        async def draft_goal(self, *, messages, current_goal):
            nonlocal calls
            calls += 1
            return await super().draft_goal(messages=messages, current_goal=current_goal)

    async def principal_provider(_: str) -> TrustedPrincipal:
        return TrustedPrincipal(user_id="alice")

    client = TestClient(build_app(repo, CountingProvider(), principal_provider=principal_provider))
    payload = {
        "messages": [{"role": "user", "content": "Build a project workspace."}],
        "current_goal": "",
    }

    missing = client.post("/onboarding/goal", json=payload)
    assert missing.status_code == 401
    assert calls == 0

    authenticated = client.post(
        "/onboarding/goal",
        headers={"Authorization": "Bearer valid-token"},
        json=payload,
    )
    assert authenticated.status_code == 200
    assert calls == 1


def test_production_runtime_rejects_local_development_identity(dsn, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCHBRO_ENV", "production")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "local")
    with pytest.raises(ValueError, match="must use ARCHBRO_AUTH_MODE=firebase"):
        build_app(_repo(dsn), FakeModelProvider())


def test_runtime_config_is_no_store_and_security_headers_are_present(dsn):
    client = TestClient(build_app(_repo(dsn), FakeModelProvider()))
    response = client.get("/runtime-config.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "https://apis.google.com" not in response.headers["content-security-policy"]
    assert "frame-src 'none'" in response.headers["content-security-policy"]
    assert '"auth_mode": "local"' in response.text


def test_required_edge_guard_blocks_direct_origin_requests(dsn, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCHBRO_ENV", "production")
    monkeypatch.setenv("ARCHBRO_AUTH_MODE", "firebase")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("ARCHBRO_FIREBASE_API_KEY", "test-browser-key")
    monkeypatch.setenv("ARCHBRO_FIREBASE_AUTH_DOMAIN", "test-project.firebaseapp.com")
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.setenv("ARCHBRO_EDGE_TOKEN", "test-edge-token")

    async def principal_provider(_: str) -> TrustedPrincipal:
        return TrustedPrincipal(user_id="alice")

    client = TestClient(
        build_app(
            _repo(dsn),
            FakeModelProvider(),
            principal_provider=principal_provider,
        )
    )

    denied = client.get("/runtime-config.js")
    wrong = client.get(
        "/runtime-config.js",
        headers={"X-ArchBro-Edge-Token": "wrong-token"},
    )
    allowed = client.get(
        "/runtime-config.js",
        headers={"X-ArchBro-Edge-Token": "test-edge-token"},
    )

    assert denied.status_code == 403
    assert wrong.status_code == 403
    assert denied.headers["x-content-type-options"] == "nosniff"
    assert allowed.status_code == 200
    assert allowed.headers["strict-transport-security"] == "max-age=86400"


def test_required_edge_guard_fails_closed_without_token(dsn, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.delenv("ARCHBRO_EDGE_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ARCHBRO_EDGE_TOKEN is required"):
        build_app(_repo(dsn), FakeModelProvider())
