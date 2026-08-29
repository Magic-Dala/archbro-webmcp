from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from archbro.platform.runtime.app import build_app
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.repository import ProjectRepository


def make_client():
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "health.db"))
    return repo, TestClient(build_app(repo, FakeModelProvider()))


def test_healthz_reports_the_process_is_serving():
    _, client = make_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_stays_healthy_when_persistence_is_unreachable():
    """Liveness must not depend on the database.

    A container healthcheck that fails on a transient database outage makes
    Docker kill and restart the app while the database is recovering, which
    turns a short outage into a restart storm. Database availability is the
    database container's own healthcheck responsibility.
    """

    repo, client = make_client()
    repo.db_path = "/nonexistent/directory/unreachable.db"

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_hidden_from_the_public_api_schema():
    _, client = make_client()

    # Assert the route exists before asserting it is absent from the schema,
    # otherwise deleting the route would leave this test passing.
    assert client.get("/healthz").status_code == 200

    schema = client.get("/openapi.json").json()

    assert "/healthz" not in schema["paths"]


def test_healthz_bypasses_the_edge_origin_guard(monkeypatch):
    """The container probe runs inside the container, so it has no edge token.

    Production puts a Cloudflare Worker in front of the origin and sets
    ARCHBRO_EDGE_GUARD=required. Without an exemption the probe gets 403, the
    container never reports healthy, and a rolling deploy stalls forever.
    """

    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.setenv("ARCHBRO_EDGE_TOKEN", "edge-token-for-this-test")
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "edge-health.db"))
    client = TestClient(build_app(repo, FakeModelProvider()))

    assert client.get("/healthz").status_code == 200


def test_edge_origin_guard_still_blocks_untokened_traffic(monkeypatch):
    monkeypatch.setenv("ARCHBRO_EDGE_GUARD", "required")
    monkeypatch.setenv("ARCHBRO_EDGE_TOKEN", "edge-token-for-this-test")
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "edge-blocked.db"))
    client = TestClient(build_app(repo, FakeModelProvider()))

    assert client.get("/").status_code == 403
    assert client.get("/runtime-config.js").status_code == 403
