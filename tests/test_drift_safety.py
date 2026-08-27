import asyncio
import sqlite3
import tempfile
from pathlib import Path

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
from archbro.platform.persistence.repository import ProjectRepository


def _repo_with_backend():
    db_path = Path(tempfile.mkdtemp()) / "m4-safety.db"
    repo = ProjectRepository(str(db_path))
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
    return repo, project, db_path


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


def _event_count(db_path: Path, project_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id=?",
            (project_id,),
        ).fetchone()[0]


def test_failed_drift_validation_does_not_persist_event_or_product_state():
    repo, project, db_path = _repo_with_backend()
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
    assert _event_count(db_path, project.id) == 0
    assert repo.list_proposals(project.id) == []


def test_successful_post_architecture_run_persists_observed_event():
    repo, project, db_path = _repo_with_backend()

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
    assert _event_count(db_path, project.id) == 1


def test_drift_policy_rejects_unknown_changed_component_before_proposal_is_saved():
    repo, project, _ = _repo_with_backend()
    context = repo.load_context(project.id)
    decision = _drift_decision(
        project.id,
        _proposal(project.id, component_id="ghost"),
    )

    with pytest.raises(ValueError, match="unknown component: ghost"):
        DriftPolicy.validate(context, decision)


def test_drift_policy_rejects_change_operation_executor_cannot_apply():
    repo, project, _ = _repo_with_backend()
    context = repo.load_context(project.id)
    decision = _drift_decision(
        project.id,
        _proposal(project.id, operation="add_component"),
    )

    with pytest.raises(ValueError, match="unsupported architecture change operation"):
        DriftPolicy.validate(context, decision)


def test_drift_policy_rejects_empty_or_unexecutable_replacement():
    repo, project, _ = _repo_with_backend()
    context = repo.load_context(project.id)

    empty_change_proposal = _proposal(project.id)
    empty_change_proposal.proposed_changes = []
    with pytest.raises(ValueError, match="at least one executable proposed change"):
        DriftPolicy.validate(context, _drift_decision(project.id, empty_change_proposal))

    missing_name = _proposal(project.id, new_name="   ")
    with pytest.raises(ValueError, match="non-empty new_name"):
        DriftPolicy.validate(context, _drift_decision(project.id, missing_name))


def test_update_task_rejects_cross_project_target_and_identity_rewrite():
    from archbro.backend.core.action_executor import ActionExecutor
    from archbro.backend.core.contracts import Task

    repo, project, _ = _repo_with_backend()
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


def test_event_and_actions_roll_back_together_when_later_write_fails():
    from archbro.backend.core.contracts import Task

    repo, project, db_path = _repo_with_backend()
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

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_proposal_insert BEFORE INSERT ON proposals "
            "BEGIN SELECT RAISE(ABORT, 'forced proposal failure'); END"
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
    assert _event_count(db_path, project.id) == 0
    assert repo.get_task(task.id).status.value == "TODO"
    assert repo.list_proposals(project.id) == []
