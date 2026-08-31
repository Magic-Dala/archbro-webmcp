import pytest

from archbro.backend.agent.evaluation import DriftPolicy
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    Project,
)
from archbro.backend.core.evaluation import (
    DriftClassification,
    DriftEvaluation,
    DriftRecommendedAction,
)
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database

pytestmark = requires_database


def _context_and_proposal(dsn):
    repo = PostgresProjectRepository(dsn)
    project = Project(name="Pending Boundary", goal="Build a project workspace.", architecture_version=1)
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(id="database", name="PostgreSQL", type="database", responsibility="Persist project state."),
            ],
        ),
    )
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        reason="The persistence requirement changed.",
        evidence=["The team explicitly selected Firestore."],
        observed_change="Persistence changes from PostgreSQL to Firestore.",
        affected_components=["database"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "database",
                "new_name": "Firestore",
                "new_type": "database",
                "new_responsibility": "Persist project state.",
            }
        ],
        impact="Persistence implementation changes after approval.",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    evaluation = DriftEvaluation(
        classification=DriftClassification.ARCHITECTURE_DRIFT,
        summary="Accepted persistence no longer matches the explicit requirement.",
        evidence=["The team explicitly selected Firestore."],
        affected_components=["database"],
        architecture_change_required=True,
        recommended_action=DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE,
    )
    proposal_action = AgentAction(
        type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
        payload={"proposal": proposal.model_dump(mode="json")},
    )
    return repo.load_context(project.id), evaluation, proposal_action


def test_architecture_drift_cannot_create_future_architecture_task_before_acceptance(dsn):
    context, evaluation, proposal_action = _context_and_proposal(dsn)
    decision = AgentDecision(
        summary="Drift plus speculative task.",
        actions=[
            AgentAction(
                type=AgentActionType.CREATE_TASK,
                payload={
                    "task": {
                        "title": "Migrate to Firestore",
                        "description": "Work that only exists if the proposal is accepted.",
                        "related_component": "database",
                    }
                },
            ),
            proposal_action,
        ],
        architecture_review_required=True,
        evaluation=evaluation,
    )

    with pytest.raises(ValueError, match="may not create tasks before human acceptance"):
        DriftPolicy.validate(context, decision)


def test_architecture_drift_cannot_relink_existing_task_before_acceptance(dsn):
    context, evaluation, proposal_action = _context_and_proposal(dsn)
    decision = AgentDecision(
        summary="Drift plus speculative relink.",
        actions=[
            AgentAction(
                type=AgentActionType.UPDATE_TASK,
                payload={
                    "task_id": "task_existing",
                    "changes": {"related_component": "database"},
                },
            ),
            proposal_action,
        ],
        architecture_review_required=True,
        evaluation=evaluation,
    )

    with pytest.raises(ValueError, match="may not relink tasks before human acceptance"):
        DriftPolicy.validate(context, decision)
