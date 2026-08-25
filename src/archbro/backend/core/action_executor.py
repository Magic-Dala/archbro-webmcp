from __future__ import annotations

import json
from datetime import datetime, timezone

from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    Component,
    ProjectStatus,
    ProposalStatus,
    Task,
    TaskProposal,
    TaskStatus,
)
from archbro.backend.core.repository import ProjectRepositoryPort


class ActionExecutor:
    def __init__(self, repository: ProjectRepositoryPort) -> None:
        self.repository = repository

    def validate_all(self, project_id: str, actions: list[AgentAction]) -> None:
        for action in actions:
            if action.type == AgentActionType.UPDATE_TASK:
                task = self.repository.get_task(action.payload["task_id"])
                changes = action.payload["changes"]
                if "status" in changes:
                    TaskStatus(changes["status"])
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

    def apply(self, project_id: str, actions: list[AgentAction]) -> list[str]:
        self.validate_all(project_id, actions)
        proposal_ids: list[str] = []
        for action in actions:
            if action.type == AgentActionType.CREATE_TASK:
                proposal = TaskProposal.model_validate(action.payload["task"])
                task = Task(**proposal.model_dump())
                self.repository.save_task(project_id, task)
            elif action.type == AgentActionType.UPDATE_TASK:
                task = self.repository.get_task(action.payload["task_id"])
                changes = dict(action.payload["changes"])
                if "status" in changes:
                    changes["status"] = TaskStatus(changes["status"])
                task = task.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
                self.repository.save_task(project_id, task)
            elif action.type == AgentActionType.ADD_PROJECT_NOTE:
                note = action.payload["note"]
                if note.startswith("INITIAL_ARCHITECTURE:"):
                    architecture = Architecture.model_validate(json.loads(note.split(":", 1)[1]))
                    current = self.repository.get_architecture(project_id)
                    if current.version != 0:
                        raise ValueError("initial architecture may only be set once")
                    project = self.repository.get_project(project_id)
                    project.architecture_version = architecture.version
                    project.updated_at = datetime.now(timezone.utc)
                    self.repository.save_architecture(project_id, architecture)
                    self.repository.save_project(project)
                else:
                    self.repository.add_note(project_id, note)
            elif action.type == AgentActionType.UPDATE_PROJECT_STATUS:
                project = self.repository.get_project(project_id)
                project.status = ProjectStatus(action.payload["status"])
                project.updated_at = datetime.now(timezone.utc)
                self.repository.save_project(project)
            elif action.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE:
                proposal = ArchitectureChangeProposal.model_validate(action.payload["proposal"])
                self.repository.save_proposal(proposal)
                proposal_ids.append(proposal.id)
            elif action.type == AgentActionType.NO_ACTION:
                continue
        return proposal_ids

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
