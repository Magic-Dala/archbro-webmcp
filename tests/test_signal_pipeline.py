"""Max-owned platform pipeline: deliver normalized external signals to the Agent.

The pipeline owns durability concerns (stable identity, retry, backoff). It does
not interpret provider payloads and it never mutates project state directly; the
AgentOrchestrator remains the only path into canonical Archbro state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import (
    Architecture,
    Component,
    Project,
    ProjectEventSource,
    ProjectEventType,
)
from archbro.backend.core.observation import ObservationInProgressError
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.pipeline.contracts import DeliveryOutcome, NormalizedSignal
from archbro.platform.pipeline.delivery import SignalDelivery
from conftest import requires_database

pytestmark = requires_database


def _repo_with_architecture(dsn) -> tuple[PostgresProjectRepository, Project]:
    repo = PostgresProjectRepository(dsn)
    project = Project(
        name="Signal Pipeline",
        goal="Keep project reality aligned with a FastAPI and PostgreSQL architecture.",
        architecture_version=1,
    )
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            summary="Accepted architecture",
            components=[
                Component(
                    id="backend",
                    name="FastAPI Backend",
                    type="backend",
                    responsibility="Serve project APIs.",
                ),
                Component(
                    id="database",
                    name="PostgreSQL",
                    type="database",
                    responsibility="Persist project state.",
                ),
            ],
        ),
    )
    return repo, project


def _github_push_signal(commit_sha: str = "abc123") -> NormalizedSignal:
    return NormalizedSignal(
        source_event_id=commit_sha,
        source=ProjectEventSource.GITHUB,
        event_type=ProjectEventType.GITHUB_CHANGE,
        payload={
            "repository": "Magic-Dala/archbro",
            "event_kind": "PUSH",
            "summary": "Backend API changed.",
            "ref": "refs/heads/main",
            "commit_sha": commit_sha,
        },
    )


def test_normalized_signal_reaches_the_agent_and_produces_a_durable_run(dsn):
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    delivery = SignalDelivery(orchestrator)

    result = asyncio.run(delivery.deliver(project.id, _github_push_signal()))

    assert result.outcome is DeliveryOutcome.APPLIED
    assert result.run is not None
    assert result.run.result == "SUCCESS"

    runs = repo.list_agent_runs(project.id)
    assert len(runs) == 1
    assert runs[0].agent_run_id == result.run.agent_run_id

    events = repo.list_events(project.id)
    assert len(events) == 1
    assert events[0].source is ProjectEventSource.GITHUB
    assert events[0].source_event_id == "abc123"


def test_redelivering_the_same_signal_does_not_produce_a_second_run(dsn):
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    delivery = SignalDelivery(orchestrator)

    first = asyncio.run(delivery.deliver(project.id, _github_push_signal()))
    second = asyncio.run(delivery.deliver(project.id, _github_push_signal()))

    assert first.outcome is DeliveryOutcome.APPLIED
    assert second.outcome is DeliveryOutcome.REPLAYED
    assert second.run is not None
    assert second.run.agent_run_id == first.run.agent_run_id

    assert len(repo.list_agent_runs(project.id)) == 1
    assert len(repo.list_events(project.id)) == 1


def test_concurrent_observation_conflict_is_retried_with_backoff(dsn):
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())

    real_observe = orchestrator.observe_event
    attempts = 0

    async def conflict_once(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ObservationInProgressError("observation is already being evaluated")
        return await real_observe(event)

    orchestrator.observe_event = conflict_once

    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    delivery = SignalDelivery(orchestrator, sleep=record_sleep, backoff_seconds=0.25)
    result = asyncio.run(delivery.deliver(project.id, _github_push_signal()))

    assert result.outcome is DeliveryOutcome.APPLIED
    assert attempts == 2
    assert slept == [0.25]
    assert len(repo.list_agent_runs(project.id)) == 1


def test_persistent_conflict_gives_up_without_raising(dsn):
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())

    attempts = 0

    async def always_conflict(event):
        nonlocal attempts
        attempts += 1
        raise ObservationInProgressError("observation is already being evaluated")

    orchestrator.observe_event = always_conflict

    async def no_sleep(seconds: float) -> None:
        return None

    delivery = SignalDelivery(
        orchestrator,
        max_attempts=3,
        sleep=no_sleep,
        backoff_seconds=0.1,
    )
    result = asyncio.run(delivery.deliver(project.id, _github_push_signal()))

    assert result.outcome is DeliveryOutcome.CONFLICT
    assert result.run is None
    assert attempts == 3
    assert repo.list_agent_runs(project.id) == []


def test_failed_agent_run_is_not_reported_as_applied(dsn):
    repo, project = _repo_with_architecture(dsn)

    class _FailingProvider(FakeModelProvider):
        async def generate(self, **kwargs):
            raise RuntimeError("provider is unavailable")

    orchestrator = AgentOrchestrator(repo, _FailingProvider())
    delivery = SignalDelivery(orchestrator)

    result = asyncio.run(delivery.deliver(project.id, _github_push_signal()))

    assert result.outcome is DeliveryOutcome.FAILED
    assert result.run is not None
    assert result.run.result == "ERROR"


def _github_merged_pr_signal(source_event_id: str, repository: str) -> NormalizedSignal:
    return NormalizedSignal(
        source_event_id=source_event_id,
        source=ProjectEventSource.GITHUB,
        event_type=ProjectEventType.GITHUB_CHANGE,
        payload={
            "repository": repository,
            "event_kind": "PULL_REQUEST_MERGED",
            "summary": f"Merged pull request in {repository}.",
            "commit_sha": f"merge-{repository[-1]}",
            "pull_request_number": 14,
        },
    )


def test_reusing_one_identity_across_repositories_is_rejected_not_crashed(dsn):
    """A bare PR number is only unique inside one repository.

    The backend replay key is ``project_id | source | source_event_id`` and does
    not include repository or connector, so two repositories reusing PR #14 map
    to the same identity with different payloads. That must surface as a terminal
    outcome rather than an exception, or one bad signal aborts the whole batch.
    """
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    delivery = SignalDelivery(orchestrator)

    first = asyncio.run(delivery.deliver(project.id, _github_merged_pr_signal("14", "org/repo-a")))
    second = asyncio.run(delivery.deliver(project.id, _github_merged_pr_signal("14", "org/repo-b")))

    assert first.outcome is DeliveryOutcome.APPLIED
    assert second.outcome is DeliveryOutcome.REJECTED
    assert second.run is None

    # The first observation keeps its original evidence; nothing is overwritten.
    events = repo.list_events(project.id)
    assert len(events) == 1
    assert events[0].payload["repository"] == "org/repo-a"


def test_provider_scoped_identities_stay_independent_across_repositories(dsn):
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    delivery = SignalDelivery(orchestrator)

    first = asyncio.run(
        delivery.deliver(
            project.id,
            _github_merged_pr_signal("github:org/repo-a:pr:14", "org/repo-a"),
        )
    )
    second = asyncio.run(
        delivery.deliver(
            project.id,
            _github_merged_pr_signal("github:org/repo-b:pr:14", "org/repo-b"),
        )
    )

    assert first.outcome is DeliveryOutcome.APPLIED
    assert second.outcome is DeliveryOutcome.APPLIED
    assert len(repo.list_events(project.id)) == 2
    assert len(repo.list_agent_runs(project.id)) == 2


def test_malformed_payload_does_not_abort_the_batch(dsn):
    """Payload validation is caught inside the orchestrator, so it surfaces as a
    failed run rather than a raised error. Either way the batch keeps going.
    """
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    delivery = SignalDelivery(orchestrator)

    malformed = NormalizedSignal(
        source_event_id="github:org/repo-a:push:bad",
        source=ProjectEventSource.GITHUB,
        event_type=ProjectEventType.GITHUB_CHANGE,
        payload={"repository": "org/repo-a", "event_kind": "PUSH", "summary": "no ref or sha"},
    )

    unhealthy = asyncio.run(delivery.deliver(project.id, malformed))
    healthy = asyncio.run(delivery.deliver(project.id, _github_push_signal("goodsha")))

    assert unhealthy.outcome is DeliveryOutcome.FAILED
    assert unhealthy.run is not None and unhealthy.run.result == "ERROR"
    assert healthy.outcome is DeliveryOutcome.APPLIED
    # Only the healthy signal became durable evidence.
    assert len(repo.list_events(project.id)) == 1


def test_unexpected_value_error_propagates_instead_of_looking_like_a_rejection(dsn):
    """Only observation-contract violations are terminal rejections.

    Persistence corruption also surfaces as ValueError (for example the Firestore
    adapter raising on a malformed idempotency key). Swallowing that as REJECTED
    would let the connector advance past corrupted state and hide the fault.
    """
    repo, project = _repo_with_architecture(dsn)
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())

    async def corrupt_store(event):
        raise ValueError("Firestore observation key is missing event_id")

    orchestrator.observe_event = corrupt_store
    delivery = SignalDelivery(orchestrator)

    with pytest.raises(ValueError, match="missing event_id"):
        asyncio.run(delivery.deliver(project.id, _github_push_signal()))


def test_payload_is_copied_so_later_mutation_cannot_change_a_delivered_observation(dsn):
    """The backend rejects a redelivery whose identity matches but data differs.

    A caller holding a reference to the original dict must not be able to turn a
    legitimate replay into a rejection.
    """
    payload = {"repository": "org/repo-a", "event_kind": "PUSH", "summary": "one", "ref": "refs/heads/main", "commit_sha": "abc123"}
    signal = NormalizedSignal(
        source_event_id="github:org/repo-a:commit:abc123",
        source=ProjectEventSource.GITHUB,
        event_type=ProjectEventType.GITHUB_CHANGE,
        payload=payload,
    )

    payload["summary"] = "mutated after construction"

    assert signal.payload["summary"] == "one"


def test_pipeline_cannot_mutate_project_state_directly(dsn):
    """The Agent boundary is the only path into canonical state.

    Import-level guard so a future change cannot quietly bypass
    AgentOrchestrator / ActionExecutor from the delivery path.
    """
    import ast
    from pathlib import Path as _Path

    import archbro.platform.pipeline as pipeline_package

    forbidden = ("action_executor", "reconciliation", "persistence.postgres")
    package_dir = _Path(pipeline_package.__file__).parent

    for module_path in package_dir.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
            elif isinstance(node, ast.Import):
                imported = ",".join(alias.name for alias in node.names)
            for pattern in forbidden:
                assert pattern not in imported, (
                    f"{module_path.name} imports {imported!r}; the pipeline must reach "
                    "canonical state only through the Agent observation boundary"
                )


def test_signal_identity_is_required_so_replay_protection_cannot_be_bypassed(dsn):
    with pytest.raises(ValueError, match="source_event_id"):
        NormalizedSignal(
            source_event_id="   ",
            source=ProjectEventSource.GITHUB,
            event_type=ProjectEventType.GITHUB_CHANGE,
            payload={"repository": "Magic-Dala/archbro"},
        )
