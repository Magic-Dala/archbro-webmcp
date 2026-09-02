"""The deployed stack has to invoke the pipeline, not just be able to.

A module entry point nobody calls leaves the pipeline exactly as unreachable as
it was before an adapter existed, so the schedule is part of the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


STACK = (
    Path(__file__).resolve().parents[1] / "deploy" / "stack" / "docker-compose.prod.yml"
)


def _stack() -> dict:
    return yaml.safe_load(STACK.read_text(encoding="utf-8"))


def test_the_deployed_stack_runs_connector_syncs_on_a_schedule():
    service = _stack()["services"]["connectors"]

    command = " ".join(service["command"]) if isinstance(service["command"], list) else service["command"]
    assert "archbro.platform.runtime.connector_sync" in command
    assert service["restart"] == "unless-stopped"


def test_the_connector_worker_runs_the_image_the_app_runs():
    services = _stack()["services"]

    assert services["connectors"]["image"] == services["app"]["image"]


def test_the_connector_worker_waits_for_the_database():
    service = _stack()["services"]["connectors"]

    assert service["depends_on"]["db"]["condition"] == "service_healthy"


def test_the_connector_worker_is_not_reachable_from_the_edge():
    # It serves nothing. Joining the tunnel network would publish a container
    # holding repository credentials for no reason.
    service = _stack()["services"]["connectors"]

    assert "edge" not in (service.get("networks") or {})


def test_one_failed_pass_does_not_end_the_schedule():
    # A repository whose token expired must not stop the loop; the next pass
    # should still run at its interval.
    service = _stack()["services"]["connectors"]
    command = " ".join(service["command"]) if isinstance(service["command"], list) else service["command"]

    assert "|| true" in command
