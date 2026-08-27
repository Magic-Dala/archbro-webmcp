import asyncio
import tempfile
from pathlib import Path

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    ArchitectureOption,
    Component,
    Project,
    ProjectEvent,
    ProjectEventType,
    ProposalStatus,
    Task,
    TaskOwner,
    TaskSource,
    TaskStatus,
)
from archbro.backend.core.evaluation import (
    DriftClassification,
    DriftEvaluation,
    DriftRecommendedAction,
)
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.provider import ModelProvider
from archbro.platform.persistence.repository import ProjectRepository


def _repo_with_project(goal: str = "Build the project."):
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "m4.db"))
    project = Project(name="M4 Test", goal=goal)
    repo.save_project(project)
    return repo, project


def test_blocked_listing_identity_is_implementation_issue_and_keeps_architecture():
    repo, project = _repo_with_project("Build an AI rental assistant.")
    architecture = Architecture(
        version=1,
        summary="Rental architecture",
        components=[
            Component(
                id="rental_domain",
                name="Rental Domain Services",
                type="service",
                kind=ArchitectureNodeKind.SERVICE,
                responsibility="Own rental capabilities.",
                children=[
                    Component(
                        id="listing_verification",
                        name="Listing Verification",
                        type="service",
                        kind=ArchitectureNodeKind.SERVICE,
                        responsibility="Verify listing identity and quality.",
                    )
                ],
            )
        ],
    )
    repo.save_architecture(project.id, architecture)
    task = Task(
        title="Implement Listing Verification",
        description="Establish a stable listing identity strategy.",
        owner=TaskOwner.HUMAN,
        source=TaskSource.ARCHITECTURE,
        related_component="listing_verification",
    )
    repo.save_task(project.id, task)

    result = asyncio.run(
        AgentOrchestrator(repo, FakeModelProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.USER_MESSAGE,
                payload={"message": "The rental provider does not expose stable listing IDs, so this task is blocked."},
            )
        )
    )

    assert result.result == "SUCCESS"
    assert result.evaluation is not None
    assert result.evaluation.classification == DriftClassification.IMPLEMENTATION_ISSUE
    assert result.evaluation.architecture_change_required is False
    assert result.evaluation.recommended_action == DriftRecommendedAction.UPDATE_TASK
    assert result.proposal_ids == []
    assert repo.list_proposals(project.id) == []
    assert repo.get_architecture(project.id).version == 1
    assert repo.get_task(task.id).status == TaskStatus.BLOCKED


def test_explicit_persistence_boundary_change_creates_proposal_without_mutating_accepted_architecture():
    repo, project = _repo_with_project(
        "Build a project app using FastAPI and PostgreSQL for persistence."
    )
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            summary="Accepted architecture",
            components=[
                Component(id="backend", name="FastAPI backend", type="backend", responsibility="Serve the API."),
                Component(id="database", name="PostgreSQL", type="database", responsibility="Persist project state."),
            ],
        ),
    )

    result = asyncio.run(
        AgentOrchestrator(repo, FakeModelProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.USER_MESSAGE,
                payload={"message": "We decided to replace PostgreSQL with Firestore for project persistence."},
            )
        )
    )

    assert result.result == "SUCCESS"
    assert result.evaluation is not None
    assert result.evaluation.classification == DriftClassification.ARCHITECTURE_DRIFT
    assert result.evaluation.architecture_change_required is True
    assert result.evaluation.recommended_action == DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE
    assert len(result.proposal_ids) == 1

    accepted = repo.get_architecture(project.id)
    assert accepted.version == 1
    assert accepted.find_component("database").name == "PostgreSQL"

    proposal = repo.get_proposal(result.proposal_ids[0])
    assert proposal.status == ProposalStatus.PENDING
    assert set(proposal.affected_components) == {"backend", "database"}


def test_internal_refactor_is_aligned_and_does_not_create_proposal():
    repo, project = _repo_with_project("Build a FastAPI project app.")
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(id="backend", name="FastAPI backend", type="backend", responsibility="Serve the API."),
            ],
        ),
    )

    before = repo.get_architecture(project.id).model_dump(mode="json")
    result = asyncio.run(
        AgentOrchestrator(repo, FakeModelProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.GITHUB_CHANGE,
                payload={
                    "message": "Refactored internal request parsing and renamed helper functions. Responsibilities and APIs are unchanged.",
                    "changed_files": ["src/archbro/backend/api/routes.py"],
                },
            )
        )
    )

    assert result.result == "SUCCESS"
    assert result.evaluation is not None
    assert result.evaluation.classification == DriftClassification.ALIGNED
    assert result.evaluation.architecture_change_required is False
    assert result.proposal_ids == []
    assert repo.list_proposals(project.id) == []
    assert repo.get_architecture(project.id).model_dump(mode="json") == before


class _ContradictoryProvider(ModelProvider):
    name = "contradictory"
    model_id = "deterministic-test"

    async def generate(self, *, event, context, system_prompt):
        proposal = ArchitectureChangeProposal(
            project_id=context.project.id,
            reason="Bad provider output.",
            evidence=["No actual architecture drift."],
            observed_change="None.",
            affected_components=["backend"],
            proposed_changes=[],
            impact="None.",
            recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
        )
        return AgentDecision(
            summary="Contradictory output",
            actions=[
                AgentAction(
                    type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
                    payload={"proposal": proposal.model_dump(mode="json")},
                )
            ],
            architecture_review_required=True,
            evaluation=DriftEvaluation(
                classification=DriftClassification.ALIGNED,
                summary="Architecture is aligned.",
                evidence=[],
                affected_components=["backend"],
                architecture_change_required=False,
                recommended_action=DriftRecommendedAction.NO_ACTION,
            ),
        )


def test_drift_policy_rejects_proposal_when_model_classifies_event_as_aligned():
    repo, project = _repo_with_project("Build a FastAPI project app.")
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(id="backend", name="FastAPI backend", type="backend", responsibility="Serve the API."),
            ],
        ),
    )
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
    assert "may not propose an architecture change" in result.error
    assert repo.snapshot(project.id) == before
    assert repo.list_proposals(project.id) == []
