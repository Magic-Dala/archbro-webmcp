import asyncio

import psycopg
import pytest

from archbro.backend.agent.evaluation import DriftPolicy
from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    Project,
    ProjectContext,
    ProjectEvent,
    ProjectEventType,
)
from archbro.backend.core.evaluation import (
    DriftClassification,
    DriftEvaluation,
    DriftRecommendedAction,
)
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.provider import ModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database

pytestmark = requires_database


def _repo_with_backend(dsn):
    repo = PostgresProjectRepository(dsn)
    project = Project(name="M4 Safety", goal="Build a project workspace.", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="backend",
                    name="Backend",
                    type="service",
                    responsibility="Serve project APIs.",
                )
            ],
        ),
    )
    return repo, project


def _proposal(project_id: str, *, operation: str = "replace_component", component_id: str = "backend", new_name: str = "Backend v2"):
    return ArchitectureChangeProposal(
        project_id=project_id,
        reason="Explicit architecture change.",
        evidence=["The requirement changed."],
        observed_change="The backend boundary changed.",
        affected_components=["backend"],
        proposed_changes=[
            {
                "operation": operation,
                "component_id": component_id,
                "new_name": new_name,
            }
        ],
        impact="Backend implementation changes.",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )


def _drift_decision(project_id: str, proposal: ArchitectureChangeProposal) -> AgentDecision:
    return AgentDecision(
        summary="Architecture drift detected.",
        actions=[
            AgentAction(
                type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
                payload={"proposal": proposal.model_dump(mode="json")},
            )
        ],
        architecture_review_required=True,
        evaluation=DriftEvaluation(
            classification=DriftClassification.ARCHITECTURE_DRIFT,
            summary="Accepted architecture no longer matches reality.",
            evidence=["The requirement changed."],
            affected_components=["backend"],
            architecture_change_required=True,
            recommended_action=DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE,
        ),
    )


class _ContradictoryProvider(ModelProvider):
    name = "contradictory"
    model_id = "deterministic-test"

    async def generate(self, *, event, context, system_prompt):
        proposal = _proposal(context.project.id)
        return AgentDecision(
            summary="Invalid proposal despite aligned evaluation.",
            actions=[
                AgentAction(
                    type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
                    payload={"proposal": proposal.model_dump(mode="json")},
                )
            ],
            architecture_review_required=True,
            evaluation=DriftEvaluation(
                classification=DriftClassification.ALIGNED,
                summary="Architecture remains aligned.",
                affected_components=["backend"],
                recommended_action=DriftRecommendedAction.NO_ACTION,
            ),
        )


def _event_count(dsn: str, project_id: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id=%s",
            (project_id,),
        ).fetchone()[0]


def test_failed_drift_validation_preserves_observation_and_run_without_product_mutation(dsn):
    repo, project = _repo_with_backend(dsn)
    before = repo.snapshot(project.id)

    result = asyncio.run(
        AgentOrchestrator(repo, _ContradictoryProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.USER_MESSAGE,
                payload={"message": "Nothing architectural changed."},
            )
        )
    )

    assert result.result == "ERROR"
    assert repo.snapshot(project.id) == before
    assert _event_count(dsn, project.id) == 1
    events = repo.list_events(project.id)
    assert len(events) == 1
    runs = repo.list_agent_runs(project.id)
    assert len(runs) == 1
    assert runs[0].event_id == events[0].id
    assert runs[0].result == "ERROR"
    assert repo.list_proposals(project.id) == []


def test_successful_post_architecture_run_persists_observed_event(dsn):
    repo, project = _repo_with_backend(dsn)

    result = asyncio.run(
        AgentOrchestrator(repo, FakeModelProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.GITHUB_CHANGE,
                payload={"message": "Internal refactor only; API and responsibilities are unchanged."},
            )
        )
    )

    assert result.result == "SUCCESS"
    assert result.evaluation is not None
    assert result.evaluation.classification == DriftClassification.ALIGNED
    assert _event_count(dsn, project.id) == 1


def test_drift_policy_rejects_unknown_changed_component_before_proposal_is_saved(dsn):
    repo, project = _repo_with_backend(dsn)
    context = repo.load_context(project.id)
    decision = _drift_decision(
        project.id,
        _proposal(project.id, component_id="ghost"),
    )

    with pytest.raises(ValueError, match="unknown component: ghost"):
        DriftPolicy.validate(context, decision)


def test_drift_policy_rejects_change_operation_executor_cannot_apply(dsn):
    repo, project = _repo_with_backend(dsn)
    context = repo.load_context(project.id)
    decision = _drift_decision(
        project.id,
        _proposal(project.id, operation="add_component"),
    )

    with pytest.raises(ValueError, match="unsupported architecture change operation"):
        DriftPolicy.validate(context, decision)


def test_drift_policy_rejects_empty_or_unexecutable_replacement(dsn):
    repo, project = _repo_with_backend(dsn)
    context = repo.load_context(project.id)

    empty_change_proposal = _proposal(project.id)
    empty_change_proposal.proposed_changes = []
    with pytest.raises(ValueError, match="at least one executable proposed change"):
        DriftPolicy.validate(context, _drift_decision(project.id, empty_change_proposal))

    missing_name = _proposal(project.id, new_name="   ")
    with pytest.raises(ValueError, match="non-empty new_name"):
        DriftPolicy.validate(context, _drift_decision(project.id, missing_name))


def test_update_task_rejects_cross_project_target_and_identity_rewrite(dsn):
    from archbro.backend.core.action_executor import ActionExecutor
    from archbro.backend.core.contracts import Task

    repo, project = _repo_with_backend(dsn)
    other = Project(name="Other", goal="Other goal", architecture_version=1)
    repo.save_project(other)
    foreign_task = Task(title="Foreign", description="Must stay foreign")
    repo.save_task(other.id, foreign_task)
    executor = ActionExecutor(repo)

    cross_project = AgentAction(
        type=AgentActionType.UPDATE_TASK,
        payload={"task_id": foreign_task.id, "changes": {"status": "DONE"}},
    )
    with pytest.raises(ValueError, match="does not belong to project"):
        executor.validate_all(project.id, [cross_project])

    local_task = Task(title="Local", description="Local task")
    repo.save_task(project.id, local_task)
    rewrite_identity = AgentAction(
        type=AgentActionType.UPDATE_TASK,
        payload={"task_id": local_task.id, "changes": {"id": foreign_task.id, "status": "DONE"}},
    )
    with pytest.raises(ValueError, match="immutable or unsupported fields: id"):
        executor.validate_all(project.id, [rewrite_identity])

    assert repo.get_task(foreign_task.id).status.value == "TODO"
    assert repo.get_task(local_task.id).id == local_task.id


def test_event_and_actions_roll_back_together_when_later_write_fails(dsn):
    from archbro.backend.core.contracts import Task

    repo, project = _repo_with_backend(dsn)
    task = Task(title="Observed task")
    repo.save_task(project.id, task)

    class _UpdateThenProposalProvider(ModelProvider):
        name = "update-then-proposal"
        model_id = "deterministic-test"

        async def generate(self, *, event, context, system_prompt):
            proposal = _proposal(project.id)
            return AgentDecision(
                summary="Update task and propose architecture change.",
                actions=[
                    AgentAction(
                        type=AgentActionType.UPDATE_TASK,
                        payload={"task_id": task.id, "changes": {"status": "DONE"}},
                    ),
                    AgentAction(
                        type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
                        payload={"proposal": proposal.model_dump(mode="json")},
                    ),
                ],
                architecture_review_required=True,
                evaluation=DriftEvaluation(
                    classification=DriftClassification.ARCHITECTURE_DRIFT,
                    summary="Architecture changed.",
                    evidence=["The requirement changed."],
                    affected_components=["backend"],
                    affected_tasks=[task.id],
                    architecture_change_required=True,
                    recommended_action=DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE,
                ),
            )

    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            CREATE FUNCTION fail_proposal_insert() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'forced proposal failure';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_proposal_insert BEFORE INSERT ON proposals
            FOR EACH ROW EXECUTE FUNCTION fail_proposal_insert();
            """
        )

    before = repo.snapshot(project.id)
    result = asyncio.run(
        AgentOrchestrator(repo, _UpdateThenProposalProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.USER_MESSAGE,
                payload={"message": "Architecture changed."},
            )
        )
    )

    assert result.result == "ERROR"
    assert "forced proposal failure" in (result.error or "")
    assert repo.snapshot(project.id) == before
    # PR3 intentionally retains the claimed event and failed AgentRun as audit history;
    # the derived project mutation still rolls back atomically.
    assert _event_count(dsn, project.id) == 1
    assert [run.result for run in repo.list_agent_runs(project.id)] == ["ERROR"]
    assert repo.get_task(task.id).status.value == "TODO"
    assert repo.list_proposals(project.id) == []
