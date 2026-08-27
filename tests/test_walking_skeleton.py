import asyncio
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import AgentAction, AgentActionType, AgentDecision, Architecture, ArchitectureChangeProposal, ArchitectureNodeKind, ArchitectureOption, Component, Project, ProjectEvent, ProjectEventType, ProposalStatus, Task, TaskStatus
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.provider import ModelProvider
from archbro.platform.persistence.repository import ProjectRepository


def make_repo():
    path = Path(tempfile.mkdtemp()) / "test.db"
    repo = ProjectRepository(str(path))
    project = Project(name="Archbro", goal="Build a collaborative project-management app using React, FastAPI, and PostgreSQL where an AI agent maintains architecture and actionable human tasks.")
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture())
    return repo, project


def bootstrap_event(project_id: str) -> ProjectEvent:
    return ProjectEvent(
        project_id=project_id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE"},
    )


def test_agent_decision_and_action_validation():
    with pytest.raises(ValidationError):
        AgentAction(type=AgentActionType.UPDATE_TASK, payload={})
    with pytest.raises(ValidationError):
        AgentDecision(summary="bad", actions=[AgentAction(type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE, payload={"proposal": {}})])


def test_scenario_a_and_b():
    repo, project = make_repo()
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())

    r1 = asyncio.run(orchestrator.observe_event(bootstrap_event(project.id)))
    assert r1.result == "SUCCESS"
    architecture = repo.get_architecture(project.id)
    assert architecture.version == 1
    assert {c.name for c in architecture.components} == {"React frontend", "FastAPI backend", "PostgreSQL"}
    tasks = repo.list_tasks(project.id)
    backend_task = next(t for t in tasks if t.related_component == "backend")

    update = ProjectEvent(project_id=project.id, type=ProjectEventType.TASK_UPDATED, payload={"task_id": backend_task.id, "status": "DONE", "message": "Backend API skeleton completed."})
    r2 = asyncio.run(orchestrator.observe_event(update))
    assert r2.result == "SUCCESS"
    assert r2.provider == "deterministic"
    assert r2.model == "human-task-transition"
    assert repo.get_task(backend_task.id).status == TaskStatus.DONE
    assert repo.get_architecture(project.id).version == 1

    change = ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE, payload={"message": "We decided to replace PostgreSQL with Firestore."})
    r3 = asyncio.run(orchestrator.observe_event(change))
    assert r3.result == "SUCCESS"
    assert r3.architecture_review_required is True
    assert len(r3.proposal_ids) == 1
    assert repo.get_architecture(project.id).version == 1
    assert any(c.name == "PostgreSQL" for c in repo.get_architecture(project.id).components)
    proposal = repo.get_proposal(r3.proposal_ids[0])
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.base_architecture_version == 1

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)
    accepted_arch = repo.get_architecture(project.id)
    assert accepted_arch.version == 2
    assert any(c.name == "Firestore" for c in accepted_arch.components)
    assert not any(c.name == "PostgreSQL" for c in accepted_arch.components)
    db_task = next(t for t in repo.list_tasks(project.id) if t.related_component == "database")
    assert db_task.status == TaskStatus.BLOCKED


def test_reject_does_not_version_architecture():
    repo, project = make_repo()
    orchestrator = AgentOrchestrator(repo, FakeModelProvider())
    asyncio.run(orchestrator.observe_event(bootstrap_event(project.id)))
    result = asyncio.run(orchestrator.observe_event(ProjectEvent(project_id=project.id, type=ProjectEventType.USER_MESSAGE, payload={"message": "We decided to replace PostgreSQL with Firestore."})))
    proposal_id = result.proposal_ids[0]
    ActionExecutor(repo).reject_proposal(project.id, proposal_id)
    assert repo.get_proposal(proposal_id).status == ProposalStatus.REJECTED
    assert repo.get_architecture(project.id).version == 1


class BrokenProvider(ModelProvider):
    name = "broken"
    model_id = "broken"

    async def generate(self, **kwargs):
        raise RuntimeError("invalid structured output")


def test_explicit_human_task_status_does_not_call_model():
    repo, project = make_repo()
    repo.save_architecture(project.id, Architecture(version=1, summary="accepted"))
    task = Task(title="Human-owned task")
    repo.save_task(project.id, task)

    result = asyncio.run(
        AgentOrchestrator(repo, BrokenProvider()).observe_event(
            ProjectEvent(
                project_id=project.id,
                type=ProjectEventType.TASK_UPDATED,
                payload={"task_id": task.id, "status": "IN_PROGRESS", "message": "Human clicked Start."},
            )
        )
    )

    assert result.result == "SUCCESS"
    assert result.provider == "deterministic"
    assert result.model == "human-task-transition"
    assert repo.get_task(task.id).status == TaskStatus.IN_PROGRESS


def test_provider_failure_does_not_mutate_state():
    repo, project = make_repo()
    before = repo.snapshot(project.id)
    result = asyncio.run(AgentOrchestrator(repo, BrokenProvider()).observe_event(bootstrap_event(project.id)))
    assert result.result == "ERROR"
    assert "invalid structured output" in result.error
    assert repo.snapshot(project.id) == before


def test_provider_abstraction():
    assert issubclass(FakeModelProvider, ModelProvider)


def test_hierarchical_architecture_contract_and_recursive_replacement():
    repo, project = make_repo()
    project.architecture_version = 1
    repo.save_project(project)
    architecture = Architecture(
        version=1,
        summary="Human-readable hierarchy",
        components=[
            Component(
                id="data",
                name="Data & State",
                type="data_boundary",
                kind=ArchitectureNodeKind.SYSTEM,
                responsibility="Own persistence and state boundaries",
                children=[
                    Component(
                        id="primary_db",
                        name="PostgreSQL",
                        type="postgresql",
                        kind=ArchitectureNodeKind.DATA_STORE,
                        responsibility="Persist domain state",
                    )
                ],
            )
        ],
    )
    repo.save_architecture(project.id, architecture)
    proposal = ArchitectureChangeProposal(
        project_id=project.id,
        base_architecture_version=1,
        reason="Move the child persistence boundary to Firestore.",
        evidence=["Human requested Firestore."],
        observed_change="Persistence technology changed.",
        affected_components=["primary_db"],
        proposed_changes=[{
            "operation": "replace_component",
            "component_id": "primary_db",
            "new_name": "Firestore",
            "new_type": "firestore",
            "new_kind": "DATA_STORE",
            "new_responsibility": "Persist domain state",
        }],
        impact="Persistence implementation changes inside Data & State.",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)
    accepted = repo.get_architecture(project.id)

    assert accepted.version == 2
    assert accepted.find_component("primary_db").name == "Firestore"
    assert accepted.root_component_id_for("primary_db") == "data"


def test_architecture_rejects_duplicate_ids_and_depth_over_three():
    with pytest.raises(ValidationError, match="duplicate architecture node id"):
        Architecture(
            version=1,
            components=[
                Component(id="same", name="A", type="service", responsibility="A"),
                Component(id="same", name="B", type="service", responsibility="B"),
            ],
        )

    with pytest.raises(ValidationError, match="depth is capped at 3"):
        Architecture(
            version=1,
            components=[
                Component(
                    id="l1",
                    name="L1",
                    type="system",
                    responsibility="L1",
                    children=[
                        Component(
                            id="l2",
                            name="L2",
                            type="service",
                            responsibility="L2",
                            children=[
                                Component(
                                    id="l3",
                                    name="L3",
                                    type="tool",
                                    responsibility="L3",
                                    children=[Component(id="l4", name="L4", type="tool", responsibility="L4")],
                                )
                            ],
                        )
                    ],
                )
            ],
        )
