from fastapi.testclient import TestClient

from archbro.platform.runtime.app import build_app
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database

pytestmark = requires_database


def make_client(dsn):
    repo = PostgresProjectRepository(dsn)
    return repo, TestClient(build_app(repo, FakeModelProvider()))


def test_healthz_reports_the_process_is_serving(dsn):
    _, client = make_client(dsn)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_stays_healthy_when_persistence_is_unreachable(dsn):
    """Liveness must not depend on the database.

    A container healthcheck that fails on a transient database outage makes
    Docker kill and restart the app while the database is recovering, which
    turns a short outage into a restart storm. Database availability is the
    database container's own healthcheck responsibility.
    """

    repo, client = make_client(dsn)
    repo.dsn = "postgresql://archbro@127.0.0.1:1/unreachable"

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_hidden_from_the_public_api_schema(dsn):
    _, client = make_client(dsn)

    # Assert the route exists before asserting it is absent from the schema,
    # otherwise deleting the route would leave this test passing.
    assert client.get("/healthz").status_code == 200

    schema = client.get("/openapi.json").json()

    assert "/healthz" not in schema["paths"]


def test_healthz_bypasses_the_edge_origin_guard(dsn, monkeypatch):
    """The container probe runs inside the container, so it has no edge token.

    Production puts a Cloudflare Worker in front of the origin and sets
    ARCHBRO_EDGE_GUARD=required. Without an exemption the probe gets 403, the
    container never reports healthy, and a rolling deploy stalls forever.
    """

    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.setenv("ARCHBRO_EDGE_TOKEN", "edge-token-for-this-test")
    repo = PostgresProjectRepository(dsn)
    client = TestClient(build_app(repo, FakeModelProvider()))

    assert client.get("/healthz").status_code == 200


def test_edge_origin_guard_still_blocks_untokened_traffic(dsn, monkeypatch):
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.setenv("ARCHBRO_EDGE_TOKEN", "edge-token-for-this-test")
    repo = PostgresProjectRepository(dsn)
    client = TestClient(build_app(repo, FakeModelProvider()))

    assert client.get("/").status_code == 403
    assert client.get("/runtime-config.js").status_code == 403
