from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    Component,
    Project,
    ProjectStatus,
    ProposalStatus,
    Task,
    TaskProposal,
    TaskStatus,
)
from archbro.backend.core.repository import ProjectRepositoryPort


@dataclass(slots=True)
class ActionMutationPlan:
    project: Project | None = None
    architecture: Architecture | None = None
    tasks: list[Task] = field(default_factory=list)
    proposals: list[ArchitectureChangeProposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def proposal_ids(self) -> list[str]:
        return [proposal.id for proposal in self.proposals]


class ActionExecutor:
    _TASK_MUTABLE_FIELDS = {
        "title",
        "description",
        "status",
        "owner",
        "related_component",
        "dependencies",
        "acceptance_criteria",
    }

    def __init__(self, repository: ProjectRepositoryPort) -> None:
        self.repository = repository

    def _task_update_from(self, task: Task, changes: dict) -> Task:
        unsupported = set(changes).difference(self._TASK_MUTABLE_FIELDS)
        if unsupported:
            raise ValueError(
                "task update contains immutable or unsupported fields: "
                + ", ".join(sorted(unsupported))
            )

        candidate = task.model_dump(mode="python")
        candidate.update(changes)
        candidate["id"] = task.id
        candidate["source"] = task.source
        candidate["created_at"] = task.created_at
        candidate["updated_at"] = datetime.now(timezone.utc)
        return Task.model_validate(candidate)

    def _validated_task_update(self, project_id: str, action: AgentAction) -> Task:
        task_id = str(action.payload["task_id"])
        if task_id not in {task.id for task in self.repository.list_tasks(project_id)}:
            raise ValueError("task does not belong to project")
        return self._task_update_from(
            self.repository.get_task(task_id),
            dict(action.payload["changes"]),
        )

    def validate_all(self, project_id: str, actions: list[AgentAction]) -> None:
        for action in actions:
            if action.type == AgentActionType.UPDATE_TASK:
                self._validated_task_update(project_id, action)
            elif action.type == AgentActionType.CREATE_TASK:
                TaskProposal.model_validate(action.payload["task"])
            elif action.type == AgentActionType.UPDATE_PROJECT_STATUS:
                ProjectStatus(action.payload["status"])
            elif action.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE:
                proposal = ArchitectureChangeProposal.model_validate(action.payload["proposal"])
                if proposal.project_id != project_id:
                    raise ValueError("proposal project_id mismatch")
                if not proposal.evidence:
                    raise ValueError("architecture change proposal requires evidence")

    def build_plan(self, project_id: str, actions: list[AgentAction]) -> ActionMutationPlan:
        self.validate_all(project_id, actions)
        current_architecture = self.repository.get_architecture(project_id)
        working_project = self.repository.get_project(project_id)
        project_changed = False
        architecture: Architecture | None = None
        task_state = {task.id: task for task in self.repository.list_tasks(project_id)}
        changed_task_ids: list[str] = []
        proposals: list[ArchitectureChangeProposal] = []
        notes: list[str] = []

        for action in actions:
            if action.type == AgentActionType.CREATE_TASK:
                task_proposal = TaskProposal.model_validate(action.payload["task"])
                task = Task(**task_proposal.model_dump())
                task_state[task.id] = task
                changed_task_ids.append(task.id)
            elif action.type == AgentActionType.UPDATE_TASK:
                task_id = str(action.payload["task_id"])
                if task_id not in task_state:
                    raise ValueError("task does not belong to project")
                task_state[task_id] = self._task_update_from(
                    task_state[task_id],
                    dict(action.payload["changes"]),
                )
                if task_id not in changed_task_ids:
                    changed_task_ids.append(task_id)
            elif action.type == AgentActionType.ADD_PROJECT_NOTE:
                note = action.payload["note"]
                if note.startswith("INITIAL_ARCHITECTURE:"):
                    if current_architecture.version != 0 or architecture is not None:
                        raise ValueError("initial architecture may only be set once")
                    architecture = Architecture.model_validate(json.loads(note.split(":", 1)[1]))
                    working_project = working_project.model_copy(
                        update={
                            "architecture_version": architecture.version,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                    project_changed = True
                else:
                    notes.append(note)
            elif action.type == AgentActionType.UPDATE_PROJECT_STATUS:
                working_project = working_project.model_copy(
                    update={
                        "status": ProjectStatus(action.payload["status"]),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                project_changed = True
            elif action.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE:
                proposals.append(
                    ArchitectureChangeProposal.model_validate(action.payload["proposal"])
                )
            elif action.type == AgentActionType.NO_ACTION:
                continue

        return ActionMutationPlan(
            project=working_project if project_changed else None,
            architecture=architecture,
            tasks=[task_state[task_id] for task_id in changed_task_ids],
            proposals=proposals,
            notes=notes,
        )

    def apply(self, project_id: str, actions: list[AgentAction]) -> list[str]:
        plan = self.build_plan(project_id, actions)
        if plan.architecture is not None:
            self.repository.save_architecture(project_id, plan.architecture)
        if plan.project is not None:
            self.repository.save_project(plan.project)
        for task in plan.tasks:
            self.repository.save_task(project_id, task)
        for proposal in plan.proposals:
            self.repository.save_proposal(proposal)
        for note in plan.notes:
            self.repository.add_note(project_id, note)
        return plan.proposal_ids

    def accept_proposal(self, project_id: str, proposal_id: str) -> ArchitectureChangeProposal:
        proposal = self.repository.get_proposal(proposal_id)
        if proposal.project_id != project_id or proposal.status != ProposalStatus.PENDING:
            raise ValueError("proposal is not pending for this project")
        architecture = self.repository.get_architecture(project_id)

        def replace_component(nodes: list[Component], change: dict) -> tuple[list[Component], bool]:
            component_id = change["component_id"]
            replaced = False
            updated: list[Component] = []
            for component in nodes:
                if component.id == component_id:
                    updated.append(component.model_copy(update={
                        "name": change["new_name"],
                        "type": change.get("new_type", component.type),
                        "responsibility": change.get("new_responsibility", component.responsibility),
                        "kind": ArchitectureNodeKind(change["new_kind"]) if change.get("new_kind") else component.kind,
                    }))
                    replaced = True
                    continue
                children, child_replaced = replace_component(component.children, change)
                updated.append(component.model_copy(update={"children": children}) if child_replaced else component)
                replaced = replaced or child_replaced
            return updated, replaced

        for change in proposal.proposed_changes:
            if change.get("operation") == "replace_component":
                component_id = change["component_id"]
                components, replaced = replace_component(architecture.components, change)
                if not replaced:
                    raise ValueError(f"affected component not found: {component_id}")
                architecture.components = components
        architecture.version += 1
        architecture.decisions.append(f"Accepted proposal {proposal.id}: {proposal.reason}")
        project = self.repository.get_project(project_id)
        project.architecture_version = architecture.version
        project.updated_at = datetime.now(timezone.utc)
        proposal.status = ProposalStatus.ACCEPTED
        self.repository.save_architecture(project_id, architecture)
        self.repository.save_project(project)
        self.repository.save_proposal(proposal)

        for task in self.repository.list_tasks(project_id):
            if task.related_component in proposal.affected_components and "PostgreSQL" in (task.title + " " + task.description):
                task.status = TaskStatus.BLOCKED
                task.description = (task.description + " Re-evaluate for accepted architecture change to Firestore.").strip()
                task.updated_at = datetime.now(timezone.utc)
                self.repository.save_task(project_id, task)
        return proposal

    def reject_proposal(self, project_id: str, proposal_id: str) -> ArchitectureChangeProposal:
        proposal = self.repository.get_proposal(proposal_id)
        if proposal.project_id != project_id or proposal.status != ProposalStatus.PENDING:
            raise ValueError("proposal is not pending for this project")
        proposal.status = ProposalStatus.REJECTED
        self.repository.save_proposal(proposal)
        return proposal
