"""Acceptance tests for the PostgreSQL persistence backend.

These run against a real PostgreSQL server -- there is no fake. The `dsn` and
`repo` fixtures come from tests/conftest.py, which gives every test its own
schema. Without DATABASE_URL the whole module skips.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.contracts import (
    AgentRunResult,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    ObservationClaimState,
    Project,
    ProjectEvent,
    ProjectEventType,
    ProposalStatus,
    Task,
    TaskStatus,
)
from archbro.backend.core.observation import (
    ObservationMutationPlan,
    ObservationRejectedError,
)
from archbro.backend.core.repository import (
    ConcurrentStateError,
    IdempotencyConflictError,
    ProjectRepositoryPort,
)
from archbro.backend.llm.fake import FakeModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from archbro.platform.runtime.app import create_app
from conftest import requires_database

pytestmark = requires_database


def _proposal(project_id: str, new_name: str = "Firestore") -> ArchitectureChangeProposal:
    return ArchitectureChangeProposal(
        project_id=project_id,
        base_architecture_version=1,
        reason="Persistence changed",
        evidence=[f"{new_name} selected"],
        observed_change=f"PostgreSQL to {new_name}",
        affected_components=["database"],
        proposed_changes=[
            {"operation": "replace_component", "component_id": "database", "new_name": new_name}
        ],
        impact="Persistence work changes",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )


def _architecture(version: int = 1) -> Architecture:
    return Architecture(
        version=version,
        components=[
            Component(
                id="database",
                name="PostgreSQL",
                type="database",
                responsibility="Persist project state.",
            )
        ],
    )


def test_postgres_repository_implements_the_full_project_repository_port(repo):
    required = {
        name
        for name in dir(ProjectRepositoryPort)
        if not name.startswith("_") and callable(getattr(ProjectRepositoryPort, name))
    }
    missing = sorted(name for name in required if not callable(getattr(repo, name, None)))
    assert missing == []
    assert len(required) == 27


def test_postgres_repository_implements_archbro_project_state_contract(repo):
    project = Project(name="Postgres QA", goal="Keep project state durable")
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
    event = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE)
    repo.save_event(event)
    repo.add_note(project.id, "first note")

    assert repo.get_project(project.id).name == "Postgres QA"
    assert repo.list_projects()[0].id == project.id
    assert repo.get_architecture(project.id).version == 1
    assert repo.list_tasks(project.id)[0].id == task.id
    assert repo.get_task(task.id).title == "Persist task"
    assert repo.get_proposal(proposal.id).id == proposal.id
    assert [item.id for item in repo.list_proposals(project.id)] == [proposal.id]
    assert repo.get_event(event.id).id == event.id
    assert [item.id for item in repo.list_events(project.id)] == [event.id]
    context = repo.load_context(project.id)
    assert context.project.id == project.id
    assert context.recent_notes == ["first note"]
    assert [item.id for item in context.pending_proposals] == [proposal.id]
    assert repo.snapshot(project.id) == repo.snapshot(project.id)

    assert repo.delete_project(project.id) is True
    assert repo.delete_project(project.id) is False
    with pytest.raises(KeyError):
        repo.get_project(project.id)
    with pytest.raises(KeyError):
        repo.get_task(task.id)
    with pytest.raises(KeyError):
        repo.get_proposal(proposal.id)
    with pytest.raises(KeyError):
        repo.get_event(event.id)
    assert repo.list_notes(project.id) == []


def test_postgres_missing_rows_return_defaults_and_errors(repo):
    assert repo.get_architecture("project_absent").version == 0
    assert repo.list_projects() == []
    assert repo.list_tasks("project_absent") == []
    assert repo.list_events("project_absent") == []
    assert repo.list_agent_runs("project_absent") == []
    with pytest.raises(KeyError):
        repo.get_project("project_absent")


def test_postgres_history_is_ordered_oldest_first_and_bounded(repo):
    project = Project(name="Bounded", goal="Bound activity history")
    repo.save_project(project)
    events = []
    for index in range(25):
        event = ProjectEvent(
            project_id=project.id,
            type=ProjectEventType.GITHUB_CHANGE,
            source="GITHUB",
            source_event_id=f"delivery-{index}",
            payload={"message": f"change-{index}"},
        )
        repo.save_event(event)
        events.append(event)
        repo.add_note(project.id, f"note-{index}")

    listed = repo.list_events(project.id, limit=5)
    assert [item.id for item in listed] == [event.id for event in events[-5:]]
    assert [item.id for item in repo.list_events(project.id)] == [event.id for event in events]
    assert repo.list_notes(project.id, limit=3) == ["note-22", "note-23", "note-24"]
    assert len(repo.list_notes(project.id)) == 20


def test_postgres_agent_run_history_is_ordered_oldest_first_and_bounded(repo):
    project = Project(name="Bounded runs", goal="Bound agent run history")
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())

    runs = []
    for index in range(25):
        event = ProjectEvent(
            project_id=project.id,
            type=ProjectEventType.GITHUB_CHANGE,
            source="GITHUB",
            source_event_id=f"delivery-{index}",
            payload={"message": f"change-{index}"},
        )
        claim = repo.claim_observation(event, run_id=f"run_{index}")
        run = AgentRunResult(
            project_id=project.id,
            event_id=claim.event.id,
            agent_run_id=claim.run_id,
            summary=f"run-{index}",
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
            result=run,
        )
        runs.append(run)

    listed = repo.list_agent_runs(project.id, limit=4)
    assert [item.agent_run_id for item in listed] == [run.agent_run_id for run in runs[-4:]]
    assert [item.agent_run_id for item in repo.list_agent_runs(project.id)] == [
        run.agent_run_id for run in runs
    ]
def test_postgres_latest_event_by_type_returns_latest_matching_event(repo):
    project = Project(name="Latest typed event", goal="Read durable artifact history by type")
    repo.save_project(project)
    first = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.CODE_ARCHITECTURE_SNAPSHOT,
        payload={"revision": "first"},
    )
    repo.save_event(first)
    repo.save_event(
        ProjectEvent(
            project_id=project.id,
            type=ProjectEventType.MANUAL_NOTE,
            payload={"message": "noise"},
        )
    )
    latest = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.CODE_ARCHITECTURE_SNAPSHOT,
        payload={"revision": "latest"},
    )
    repo.save_event(latest)

    assert repo.get_latest_event_by_type(
        project.id,
        ProjectEventType.CODE_ARCHITECTURE_SNAPSHOT,
    ) == latest
    assert repo.get_latest_event_by_type(project.id, ProjectEventType.TASK_UPDATED) is None


def test_postgres_save_event_deduplicates_on_source_key(repo):
    project = Project(name="Dedupe", goal="Dedupe deliveries")
    repo.save_project(project)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-1",
        payload={"message": "same"},
    )
    repo.save_event(event)
    repo.save_event(event.model_copy(update={"id": "event_second_delivery"}))
    assert len(repo.list_events(project.id)) == 1

    conflicting = event.model_copy(
        update={"id": "event_third", "payload": {"message": "different"}}
    )
    with pytest.raises(ObservationRejectedError, match="different observation data"):
        repo.save_event(conflicting)
    assert len(repo.list_events(project.id)) == 1



def test_postgres_claim_observation_rejects_identity_collision_with_typed_error(repo):
    project = Project(name="Typed rejection", goal="Preserve delivery contract")
    repo.save_project(project)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="github:repo:pr:42",
        payload={"message": "original"},
    )
    repo.claim_observation(event, run_id="run_original")

    conflicting = event.model_copy(
        update={"id": "event_conflicting", "payload": {"message": "different"}}
    )
    with pytest.raises(ObservationRejectedError, match="different observation data"):
        repo.claim_observation(conflicting, run_id="run_conflicting")

def test_postgres_source_key_uniqueness_is_enforced_by_the_database(repo, dsn):
    """The unique index is the last line of defence against duplicate events.

    save_event serialises concurrent writers by taking the project row FOR
    UPDATE, but it discards the lock result: when no project row exists there is
    nothing to lock, and two concurrent writers reach the insert together. The
    index is then the only thing left, so assert the database enforces it rather
    than trusting the application-level check that runs first.
    """

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'idx_events_source_key' AND schemaname = current_schema()"
        ).fetchone()

    assert row is not None, "idx_events_source_key was not created"
    assert "UNIQUE" in row[0].upper(), f"index is not unique: {row[0]}"


def test_postgres_database_rejects_a_duplicate_source_key_without_the_application_check(repo, dsn):
    """Write straight to the table, bypassing save_event's own SELECT.

    This is the path a concurrent writer takes when the application check has
    already passed for both of them.
    """

    with psycopg.connect(dsn, autocommit=True) as conn:
        insert = "INSERT INTO events (id, project_id, data, source_key) VALUES (%s, %s, %s, %s)"
        conn.execute(insert, ("event-1", "project-1", "{}", "github:pr-42"))

        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, ("event-2", "project-1", "{}", "github:pr-42"))

    # The index is partial (WHERE source_key IS NOT NULL), so events without a
    # source key must stay unconstrained.
    with psycopg.connect(dsn, autocommit=True) as conn:
        insert = "INSERT INTO events (id, project_id, data, source_key) VALUES (%s, %s, %s, %s)"
        conn.execute(insert, ("event-3", "project-1", "{}", None))
        conn.execute(insert, ("event-4", "project-1", "{}", None))


def test_postgres_acceptance_commits_architecture_project_tasks_and_proposal_atomically(repo):
    project = Project(name="Postgres M5", goal="Reconcile acceptance", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    task = Task(title="Prepare PostgreSQL persistence", related_component="database")
    repo.save_task(project.id, task)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == 2
    assert repo.get_project(project.id).architecture_version == 2
    assert repo.get_task(task.id).status == TaskStatus.BLOCKED
    assert repo.get_proposal(proposal.id).status == ProposalStatus.ACCEPTED


def test_postgres_acceptance_rejects_a_stale_expected_architecture_version(repo):
    project = Project(name="Stale Base", goal="Reject stale acceptance", architecture_version=2)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture(version=2))
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    with pytest.raises(ValueError, match="accepted architecture changed before proposal commit"):
        repo.save_acceptance_state(
            project_id=project.id,
            expected_architecture_version=1,
            expected_task_updated_at={},
            project=project.model_copy(update={"architecture_version": 2}),
            architecture=_architecture(version=2),
            tasks=[],
            proposal=proposal.model_copy(update={"status": ProposalStatus.ACCEPTED}),
        )

    assert repo.get_architecture(project.id).version == 2
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_postgres_acceptance_rejects_a_concurrently_updated_task(repo):
    project = Project(name="Task Race", goal="Protect human task state", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    task = Task(title="Validate persistence", status=TaskStatus.IN_PROGRESS, related_component="database")
    repo.save_task(project.id, task)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    stale_updated_at = task.updated_at.isoformat()
    repo.save_task(
        project.id,
        task.model_copy(update={"status": TaskStatus.DONE, "updated_at": task.updated_at + timedelta(seconds=1)}),
    )

    with pytest.raises(ValueError, match="acceptance task changed before proposal commit"):
        repo.save_acceptance_state(
            project_id=project.id,
            expected_architecture_version=1,
            expected_task_updated_at={task.id: stale_updated_at},
            project=project.model_copy(update={"architecture_version": 2}),
            architecture=_architecture(version=2),
            tasks=[task.model_copy(update={"status": TaskStatus.BLOCKED})],
            proposal=proposal.model_copy(update={"status": ProposalStatus.ACCEPTED}),
        )

    assert repo.get_task(task.id).status == TaskStatus.DONE
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING
    assert repo.get_architecture(project.id).version == 1


def test_postgres_reject_cannot_overwrite_a_concurrent_accept(repo):
    project = Project(name="Decision", goal="Serialize review", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)
    assert repo.get_proposal(proposal.id).status == ProposalStatus.ACCEPTED

    with pytest.raises(ValueError, match="proposal status changed before decision commit"):
        repo.save_proposal_decision(
            project_id=project.id,
            proposal=proposal.model_copy(update={"status": ProposalStatus.REJECTED}),
            expected_status=ProposalStatus.PENDING,
        )
    assert repo.get_proposal(proposal.id).status == ProposalStatus.ACCEPTED

    with pytest.raises(KeyError):
        repo.save_proposal_decision(
            project_id=project.id,
            proposal=_proposal(project.id).model_copy(update={"id": "proposal_absent"}),
            expected_status=ProposalStatus.PENDING,
        )


def test_postgres_observation_source_key_replays_one_durable_run(repo):
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
        event=claim.event, run_id=claim.run_id, plan=ObservationMutationPlan(), result=result
    )

    replay = repo.claim_observation(
        event.model_copy(update={"id": "event_second_delivery"}), run_id="run_should_not_execute"
    )
    assert replay.state == ObservationClaimState.REPLAY
    assert replay.event.id == claim.event.id
    assert replay.existing_result is not None
    assert replay.existing_result.agent_run_id == "run_trace"
    assert len(repo.list_events(project.id)) == 1
    assert len(repo.list_agent_runs(project.id)) == 1


def test_postgres_failed_observation_can_be_reclaimed_without_duplicate_event(repo):
    project = Project(name="Retry", goal="Retry observations", architecture_version=1)
    repo.save_project(project)
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
        event.model_copy(update={"id": "event_retry_request"}), run_id="run_retry"
    )
    assert retry.state == ObservationClaimState.CLAIMED
    assert retry.event.id == claim.event.id
    assert retry.run_id == "run_retry"
    assert len(repo.list_events(project.id)) == 1
    assert [run.result for run in repo.list_agent_runs(project.id)] == ["ERROR"]


def test_postgres_claim_observation_requires_an_existing_project(repo):
    event = ProjectEvent(project_id="project_absent", type=ProjectEventType.USER_MESSAGE)
    with pytest.raises(KeyError):
        repo.claim_observation(event, run_id="run_orphan")


def test_postgres_observation_commit_rejects_a_lost_claim(repo):
    project = Project(name="Lost Claim", goal="Guard the claim", architecture_version=1)
    repo.save_project(project)
    event = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE)
    claim = repo.claim_observation(event, run_id="run_owner")
    result = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id="run_thief",
        summary="stolen",
        actions=[],
        architecture_review_required=False,
        provider="fake",
        model="fake",
        result="SUCCESS",
    )

    with pytest.raises(ValueError, match="observation processing claim changed before commit"):
        repo.commit_observation_result(
            event=claim.event, run_id="run_thief", plan=ObservationMutationPlan(), result=result
        )
    assert repo.list_agent_runs(project.id) == []


def test_postgres_observation_commit_rejects_stale_task_plan_and_writes_nothing(repo):
    project = Project(name="Observation Race", goal="Protect task state", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture(version=1))
    task = Task(title="Human task", status=TaskStatus.TODO)
    repo.save_task(project.id, task)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        source="HUMAN",
        source_event_id="postgres-stale-observation",
        payload={"message": "Update the task."},
    )
    claim = repo.claim_observation(event, run_id="run_stale")
    plan = ObservationMutationPlan(
        tasks=[task.model_copy(update={"status": TaskStatus.IN_PROGRESS})],
        notes=["should not be written"],
        expected_task_updated_at={task.id: task.updated_at.isoformat()},
    )
    current = repo.get_task(task.id)
    repo.save_task(
        project.id,
        current.model_copy(
            update={"status": TaskStatus.DONE, "updated_at": current.updated_at + timedelta(seconds=1)}
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
            event=claim.event, run_id=claim.run_id, plan=plan, result=result
        )

    assert repo.get_task(task.id).status == TaskStatus.DONE
    assert repo.list_agent_runs(project.id) == []
    assert repo.list_notes(project.id) == []


def test_postgres_observation_commit_applies_the_whole_plan(repo):
    project = Project(name="Observation Commit", goal="Materialize the plan", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-plan",
        payload={"message": "database changed"},
    )
    claim = repo.claim_observation(event, run_id="run_plan")
    proposal = _proposal(project.id).model_copy(update={"evidence_event_ids": [claim.event.id]})
    new_task = Task(title="Follow up on drift")
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
    repo.commit_observation_result(
        event=claim.event,
        run_id=claim.run_id,
        plan=ObservationMutationPlan(
            architecture=_architecture(version=2),
            tasks=[new_task],
            proposals=[proposal],
            notes=["drift observed"],
            expected_architecture_version=1,
        ),
        result=result,
    )

    assert repo.get_architecture(project.id).version == 2
    assert [item.id for item in repo.list_proposals(project.id)] == [proposal.id]
    assert [item.id for item in repo.list_tasks(project.id)] == [new_task.id]
    assert repo.list_notes(project.id) == ["drift observed"]
    assert [run.agent_run_id for run in repo.list_agent_runs(project.id)] == ["run_plan"]


def test_postgres_observation_commit_rejects_foreign_proposal_evidence(repo):
    project = Project(name="Evidence Guard", goal="Guard evidence", architecture_version=1)
    repo.save_project(project)
    other = Project(name="Other", goal="Other project")
    repo.save_project(other)
    foreign_event = ProjectEvent(project_id=other.id, type=ProjectEventType.USER_MESSAGE)
    repo.save_event(foreign_event)

    event = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE)
    claim = repo.claim_observation(event, run_id="run_evidence")
    proposal = _proposal(project.id).model_copy(update={"evidence_event_ids": [foreign_event.id]})
    result = AgentRunResult(
        project_id=project.id,
        event_id=claim.event.id,
        agent_run_id=claim.run_id,
        summary="foreign evidence",
        actions=[],
        architecture_review_required=True,
        proposal_ids=[proposal.id],
        provider="fake",
        model="fake",
        result="SUCCESS",
    )

    with pytest.raises(ValueError, match="proposal evidence must reference an event from the same project"):
        repo.commit_observation_result(
            event=claim.event,
            run_id=claim.run_id,
            plan=ObservationMutationPlan(proposals=[proposal]),
            result=result,
        )
    assert repo.list_proposals(project.id) == []
    assert repo.list_agent_runs(project.id) == []


def test_postgres_commit_event_actions_is_atomic_on_a_rejected_mutation(repo):
    project = Project(name="Atomic", goal="Keep event and mutations together")
    repo.save_project(project)
    task = Task(title="Original task")
    repo.save_task(project.id, task)
    event = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE)
    foreign_proposal = _proposal("project_somewhere_else")

    with pytest.raises(ValueError, match="proposal project_id mismatch during event commit"):
        repo.commit_event_actions(
            event=event,
            project=None,
            architecture=None,
            tasks=[task.model_copy(update={"title": "Changed task"})],
            proposals=[foreign_proposal],
            notes=["atomic note"],
        )

    assert repo.get_task(task.id).title == "Original task"
    assert repo.list_events(project.id) == []
    assert repo.list_notes(project.id) == []

    repo.commit_event_actions(
        event=event,
        project=None,
        architecture=_architecture(),
        tasks=[task.model_copy(update={"title": "Changed task"})],
        proposals=[_proposal(project.id)],
        notes=["atomic note"],
    )
    assert repo.get_task(task.id).title == "Changed task"
    assert [item.id for item in repo.list_events(project.id)] == [event.id]
    assert repo.list_notes(project.id) == ["atomic note"]
    assert repo.get_architecture(project.id).version == 1

    with pytest.raises(KeyError):
        repo.commit_event_actions(
            event=ProjectEvent(project_id="project_absent", type=ProjectEventType.USER_MESSAGE),
            project=None,
            architecture=None,
            tasks=[],
            proposals=[],
            notes=[],
        )


def test_postgres_commit_event_actions_rolls_back_task_and_event_after_injected_write_failure(repo, monkeypatch):
    project = Project(name="Atomic task event", goal="Never expose a partial semantic task mutation")
    repo.save_project(project)
    task = Task(title="Atomic semantic task")
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.MANUAL_NOTE,
        payload={"intent": "CREATE_TASK", "task_id": task.id},
    )
    original_put_task = repo._put_task

    def fail_after_task_write(conn, project_id, candidate):
        original_put_task(conn, project_id, candidate)
        raise RuntimeError("injected failure after task write")

    monkeypatch.setattr(repo, "_put_task", fail_after_task_write)
    with pytest.raises(RuntimeError, match="injected failure"):
        repo.commit_event_actions(
            event=event,
            project=None,
            architecture=None,
            tasks=[task],
            proposals=[],
            notes=[],
        )

    assert repo.list_tasks(project.id) == []
    assert repo.list_events(project.id) == []


def test_postgres_commit_event_actions_rejects_stale_task_plan_without_audit_event(repo):
    project = Project(name="Stale task guard", goal="Reject stale semantic task plans")
    repo.save_project(project)
    original = Task(title="Start deployment")
    repo.save_task(project.id, original)
    expected_updated_at = original.updated_at.isoformat()

    concurrent = original.model_copy(
        update={
            "status": TaskStatus.IN_PROGRESS,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )
    repo.save_task(project.id, concurrent)
    stale_candidate = original.model_copy(
        update={
            "status": TaskStatus.BLOCKED,
            "updated_at": original.updated_at + timedelta(seconds=2),
        }
    )
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.TASK_UPDATED,
        payload={"intent": "TASK_STATUS_TRANSITION", "task_id": original.id},
    )

    with pytest.raises(ConcurrentStateError, match="task state changed before commit"):
        repo.commit_event_actions(
            event=event,
            project=None,
            architecture=None,
            tasks=[stale_candidate],
            proposals=[],
            notes=[],
            expected_task_updated_at={original.id: expected_updated_at},
        )

    assert repo.get_task(original.id).status == TaskStatus.IN_PROGRESS
    assert repo.list_events(project.id) == []


def test_postgres_commit_event_actions_dedupes_semantic_create_by_source_event_id(repo):
    project = Project(name="Idempotent task", goal="Retry a committed create safely")
    repo.save_project(project)
    first_task = Task(title="One logical task")
    first_event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.MANUAL_NOTE,
        source="SYSTEM",
        source_event_id="semantic-task-create:req-123",
        payload={
            "intent": "CREATE_TASK",
            "request_fingerprint": "same-request",
            "task_id": first_task.id,
        },
    )
    canonical = repo.commit_event_actions(
        event=first_event,
        project=None,
        architecture=None,
        tasks=[first_task],
        proposals=[],
        notes=[],
    )
    assert canonical.id == first_event.id

    retry_task = Task(title="One logical task")
    retry_event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.MANUAL_NOTE,
        source="SYSTEM",
        source_event_id="semantic-task-create:req-123",
        payload={
            "intent": "CREATE_TASK",
            "request_fingerprint": "same-request",
            "task_id": retry_task.id,
        },
    )
    canonical_retry = repo.commit_event_actions(
        event=retry_event,
        project=None,
        architecture=None,
        tasks=[retry_task],
        proposals=[],
        notes=[],
    )
    assert canonical_retry.id == first_event.id
    assert canonical_retry.payload["task_id"] == first_task.id
    assert [task.id for task in repo.list_tasks(project.id)] == [first_task.id]
    assert [event.id for event in repo.list_events(project.id)] == [first_event.id]

    conflicting_event = retry_event.model_copy(
        update={
            "id": "event_conflict",
            "payload": {
                "intent": "CREATE_TASK",
                "request_fingerprint": "different-request",
                "task_id": retry_task.id,
            },
        }
    )
    with pytest.raises(IdempotencyConflictError, match="different semantic request"):
        repo.commit_event_actions(
            event=conflicting_event,
            project=None,
            architecture=None,
            tasks=[retry_task],
            proposals=[],
            notes=[],
        )
    assert [task.id for task in repo.list_tasks(project.id)] == [first_task.id]
    assert [event.id for event in repo.list_events(project.id)] == [first_event.id]


def _run_concurrently(*targets):
    """Run callables on real threads, released together, and collect outcomes."""
    barrier = threading.Barrier(len(targets))
    outcomes: list[object] = [None] * len(targets)

    def wrap(index, target):
        def run():
            barrier.wait(timeout=10)
            try:
                outcomes[index] = target()
            except Exception as error:  # noqa: BLE001 - the outcome is the assertion
                outcomes[index] = error

        return run

    threads = [threading.Thread(target=wrap(index, target)) for index, target in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "concurrent repository call deadlocked"
    return outcomes


def test_postgres_concurrent_claims_of_one_observation_elect_a_single_runner(repo):
    project = Project(name="Concurrent Claim", goal="Elect one runner", architecture_version=1)
    repo.save_project(project)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-concurrent",
        payload={"message": "raced delivery"},
    )

    outcomes = _run_concurrently(
        lambda: repo.claim_observation(event.model_copy(update={"id": "event_a"}), run_id="run_a"),
        lambda: repo.claim_observation(event.model_copy(update={"id": "event_b"}), run_id="run_b"),
    )
    for outcome in outcomes:
        assert not isinstance(outcome, Exception), outcome

    states = sorted(outcome.state for outcome in outcomes)
    assert states == [ObservationClaimState.CLAIMED, ObservationClaimState.IN_PROGRESS]
    assert len({outcome.event.id for outcome in outcomes}) == 1
    assert len(repo.list_events(project.id)) == 1

    claimed = next(o for o in outcomes if o.state == ObservationClaimState.CLAIMED)
    in_progress = next(o for o in outcomes if o.state == ObservationClaimState.IN_PROGRESS)
    assert in_progress.run_id == claimed.run_id


def test_postgres_expired_claim_can_be_taken_over_by_another_run(repo, dsn):
    project = Project(name="Stale Claim", goal="Recover abandoned runs", architecture_version=1)
    repo.save_project(project)
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.GITHUB_CHANGE,
        source="GITHUB",
        source_event_id="delivery-abandoned",
        payload={"message": "worker died"},
    )
    first = repo.claim_observation(event, run_id="run_abandoned")
    assert first.state == ObservationClaimState.CLAIMED

    fresh = repo.claim_observation(event, run_id="run_too_early")
    assert fresh.state == ObservationClaimState.IN_PROGRESS
    assert fresh.run_id == "run_abandoned"

    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE event_processing SET updated_at=%s WHERE event_id=%s", (expired, first.event.id)
        )

    stolen = repo.claim_observation(event, run_id="run_takeover")
    assert stolen.state == ObservationClaimState.CLAIMED
    assert stolen.run_id == "run_takeover"

    result = AgentRunResult(
        project_id=project.id,
        event_id=first.event.id,
        agent_run_id="run_abandoned",
        summary="too late",
        actions=[],
        architecture_review_required=False,
        provider="fake",
        model="fake",
        result="SUCCESS",
    )
    with pytest.raises(ValueError, match="observation processing claim changed before commit"):
        repo.commit_observation_result(
            event=first.event, run_id="run_abandoned", plan=ObservationMutationPlan(), result=result
        )


def test_postgres_concurrent_acceptances_let_exactly_one_version_win(repo):
    project = Project(name="Accept Race", goal="Serialize approval", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    first = _proposal(project.id, "Firestore")
    second = _proposal(project.id, "Spanner")
    repo.save_proposal(first)
    repo.save_proposal(second)

    def accept(proposal, name):
        return repo.save_acceptance_state(
            project_id=project.id,
            expected_architecture_version=1,
            expected_task_updated_at={},
            project=project.model_copy(update={"architecture_version": 2}),
            architecture=_architecture(version=2).model_copy(
                update={"components": [_architecture().components[0].model_copy(update={"name": name})]}
            ),
            tasks=[],
            proposal=proposal.model_copy(update={"status": ProposalStatus.ACCEPTED}),
        )

    outcomes = _run_concurrently(
        lambda: accept(first, "Firestore"), lambda: accept(second, "Spanner")
    )
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(failures) == 1
    assert "accepted architecture changed before proposal commit" in str(failures[0])

    architecture = repo.get_architecture(project.id)
    assert architecture.version == 2
    winner_name = architecture.find_component("database").name
    accepted = [p for p in repo.list_proposals(project.id) if p.status == ProposalStatus.ACCEPTED]
    assert len(accepted) == 1
    assert winner_name in accepted[0].observed_change


def test_postgres_concurrent_proposal_decisions_keep_the_first_writer(repo):
    project = Project(name="Decision Race", goal="Serialize review", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(project.id, _architecture())
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    def decide(status):
        return repo.save_proposal_decision(
            project_id=project.id,
            proposal=proposal.model_copy(update={"status": status}),
            expected_status=ProposalStatus.PENDING,
        )

    outcomes = _run_concurrently(
        lambda: decide(ProposalStatus.ACCEPTED), lambda: decide(ProposalStatus.REJECTED)
    )
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(failures) == 1
    assert "proposal status changed before decision commit" in str(failures[0])
    assert repo.get_proposal(proposal.id).status in {ProposalStatus.ACCEPTED, ProposalStatus.REJECTED}


def test_runtime_selects_postgres_when_configured(monkeypatch, dsn):
    monkeypatch.setenv("ARCHBRO_PERSISTENCE", "postgres")
    monkeypatch.setenv("DATABASE_URL", dsn)

    app = create_app(provider=FakeModelProvider())
    http = TestClient(app)
    response = http.post(
        "/projects", json={"name": "Postgres Project", "goal": "Use PostgreSQL", "description": ""}
    )
    assert response.status_code == 200
    project_id = response.json()["id"]
    assert http.get(f"/projects/{project_id}").status_code == 200
    assert project_id in [item["id"] for item in http.get("/projects").json()]

    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone()
    assert row is not None


def test_runtime_rejects_postgres_without_a_database_url(monkeypatch):
    monkeypatch.setenv("ARCHBRO_PERSISTENCE", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_app(provider=FakeModelProvider())


def test_runtime_rejects_an_unknown_persistence_mode(monkeypatch):
    monkeypatch.setenv("ARCHBRO_PERSISTENCE", "cassandra")

    with pytest.raises(ValueError, match="must be 'postgres'"):
        create_app(provider=FakeModelProvider())
