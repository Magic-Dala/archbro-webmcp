from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    Component,
    Task,
    TaskOwner,
    TaskSource,
    TaskStatus,
)


@dataclass(frozen=True)
class AcceptanceReconciliationPlan:
    architecture: Architecture
    updated_tasks: tuple[Task, ...]
    created_tasks: tuple[Task, ...]


class ArchitectureAcceptanceReconciler:
    """Build the deterministic state transition produced by accepting a proposal.

    The reconciler is deliberately model-free. A human has already approved the
    proposal, so acceptance applies that exact architecture decision and derives
    only the minimum task changes needed to make execution match the new accepted
    architecture.
    """

    @staticmethod
    def _replace_component(
        nodes: list[Component],
        change: dict,
    ) -> tuple[list[Component], Component | None, Component | None]:
        component_id = str(change["component_id"])
        before: Component | None = None
        after: Component | None = None
        updated: list[Component] = []

        for component in nodes:
            if component.id == component_id:
                before = component
                new_type = change.get("new_type")
                replacement_type = (
                    str(new_type).strip() if new_type is not None and str(new_type).strip() else component.type
                )
                new_responsibility = change.get("new_responsibility")
                replacement_responsibility = (
                    str(new_responsibility).strip()
                    if new_responsibility is not None and str(new_responsibility).strip()
                    else component.responsibility
                )
                replacement = component.model_copy(
                    update={
                        "name": str(change["new_name"]).strip(),
                        "type": replacement_type,
                        "responsibility": replacement_responsibility,
                        "kind": (
                            ArchitectureNodeKind(str(change["new_kind"]))
                            if change.get("new_kind")
                            else component.kind
                        ),
                    },
                    deep=True,
                )
                after = replacement
                updated.append(replacement)
                continue

            children, child_before, child_after = ArchitectureAcceptanceReconciler._replace_component(
                component.children,
                change,
            )
            if child_before is not None:
                before = child_before
                after = child_after
                updated.append(component.model_copy(update={"children": children}, deep=True))
            else:
                updated.append(component.model_copy(deep=True))

        return updated, before, after

    @staticmethod
    def _migration_task_title(before: Component, after: Component) -> str:
        if before.name.strip().casefold() != after.name.strip().casefold():
            return f"Migrate {before.name} to {after.name}"
        return f"Implement accepted architecture change for {after.name}"

    @staticmethod
    def _append_reconciliation_note(task: Task, note: str, now: datetime) -> Task:
        description = task.description.strip()
        if note not in description:
            description = f"{description} {note}".strip()
        return task.model_copy(
            update={
                "status": TaskStatus.BLOCKED,
                "description": description,
                "updated_at": now,
            }
        )

    def build_plan(
        self,
        *,
        architecture: Architecture,
        proposal: ArchitectureChangeProposal,
        tasks: list[Task],
    ) -> AcceptanceReconciliationPlan:
        if not proposal.proposed_changes:
            raise ValueError("accepted architecture proposal requires at least one proposed change")

        next_architecture = architecture.model_copy(deep=True)
        changed_components: dict[str, tuple[Component, Component]] = {}

        for change in proposal.proposed_changes:
            supported_keys = {
                "operation",
                "component_id",
                "new_name",
                "new_type",
                "new_responsibility",
                "new_kind",
            }
            unsupported_keys = set(change).difference(supported_keys)
            if unsupported_keys:
                raise ValueError(
                    "replace_component contains unsupported fields: "
                    + ", ".join(sorted(unsupported_keys))
                )
            operation = str(change.get("operation", "")).strip()
            if operation != "replace_component":
                raise ValueError(f"unsupported architecture change operation: {operation or '<missing>'}")

            component_id = str(change.get("component_id", "")).strip()
            if not component_id:
                raise ValueError("replace_component requires component_id")
            if component_id in changed_components:
                raise ValueError(f"proposal changes component more than once: {component_id}")
            if component_id not in proposal.affected_components:
                raise ValueError("changed component must be included in proposal affected_components")
            if not str(change.get("new_name", "")).strip():
                raise ValueError("replace_component requires a non-empty new_name")

            components, before, after = self._replace_component(next_architecture.components, change)
            if before is None or after is None:
                raise ValueError(f"affected component not found: {component_id}")
            if before == after:
                raise ValueError(f"proposal replacement is a no-op for component: {component_id}")
            next_architecture.components = components
            changed_components[component_id] = (before, after)

        unknown_affected = set(proposal.affected_components).difference(next_architecture.component_ids())
        if unknown_affected:
            raise ValueError(
                "proposal references unknown affected components: "
                + ", ".join(sorted(unknown_affected))
            )

        next_architecture.version = architecture.version + 1
        next_architecture.decisions.append(
            f"Accepted proposal {proposal.id}: {proposal.reason}"
        )
        # Re-run the Architecture validators after all replacements have been applied.
        next_architecture = Architecture.model_validate(next_architecture.model_dump(mode="python"))

        now = datetime.now(timezone.utc)
        updated_tasks: list[Task] = []
        affected = set(proposal.affected_components)
        migration_titles = {
            component_id: self._migration_task_title(before, after)
            for component_id, (before, after) in changed_components.items()
        }

        for task in tasks:
            if task.related_component not in affected or task.status == TaskStatus.DONE:
                continue

            replacement = changed_components.get(task.related_component or "")
            if replacement is not None:
                aligned_title = migration_titles[task.related_component or ""]
                if task.title.strip().casefold() == aligned_title.strip().casefold():
                    # Work already written for the accepted replacement remains actionable.
                    continue
                before, after = replacement
                note = (
                    f"Accepted architecture changed {before.name} to {after.name}; "
                    "re-evaluate this task before continuing."
                )
            else:
                note = (
                    "Accepted architecture change impacts this component; "
                    "re-evaluate this task before continuing."
                )
            updated_tasks.append(self._append_reconciliation_note(task, note, now))

        created_tasks: list[Task] = []
        existing_task_keys = {
            (task.related_component, task.title.strip().casefold())
            for task in tasks
        }
        for component_id, (before, after) in changed_components.items():
            title = migration_titles[component_id]
            task_key = (component_id, title.strip().casefold())
            if task_key in existing_task_keys:
                continue

            responsibility = after.responsibility.strip()
            description = (
                f"Implement the accepted architecture change replacing {before.name} "
                f"with {after.name} for component {component_id}."
            )
            if responsibility:
                description += f" Accepted responsibility: {responsibility}."

            acceptance_criteria = [
                f"{after.name} is implemented as the accepted replacement for {before.name}.",
            ]
            if responsibility:
                acceptance_criteria.append(
                    f"The replacement fulfills the accepted responsibility: {responsibility}."
                )

            created_tasks.append(
                Task(
                    title=title,
                    description=description,
                    status=TaskStatus.TODO,
                    owner=TaskOwner.HUMAN,
                    source=TaskSource.ARCHITECTURE,
                    related_component=component_id,
                    acceptance_criteria=acceptance_criteria,
                    created_at=now,
                    updated_at=now,
                )
            )
            existing_task_keys.add(task_key)

        return AcceptanceReconciliationPlan(
            architecture=next_architecture,
            updated_tasks=tuple(updated_tasks),
            created_tasks=tuple(created_tasks),
        )
