from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    Component,
    Relationship,
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
    """Build one deterministic, model-free accepted architecture transition."""

    @staticmethod
    def _component_ids(component: Component) -> set[str]:
        ids = {component.id}
        for child in component.children:
            ids.update(ArchitectureAcceptanceReconciler._component_ids(child))
        return ids

    @staticmethod
    def _replace_component(
        nodes: list[Component],
        component_id: str,
        replacement: Component,
    ) -> tuple[list[Component], Component | None]:
        before: Component | None = None
        updated: list[Component] = []
        for component in nodes:
            if component.id == component_id:
                before = component
                updated.append(replacement)
                continue
            children, child_before = ArchitectureAcceptanceReconciler._replace_component(
                component.children,
                component_id,
                replacement,
            )
            if child_before is not None:
                before = child_before
                updated.append(component.model_copy(update={"children": children}, deep=True))
            else:
                updated.append(component.model_copy(deep=True))
        return updated, before

    @staticmethod
    def _remove_component(
        nodes: list[Component],
        component_id: str,
    ) -> tuple[list[Component], Component | None, set[str]]:
        removed: Component | None = None
        removed_ids: set[str] = set()
        updated: list[Component] = []
        for component in nodes:
            if component.id == component_id:
                removed = component
                removed_ids.update(ArchitectureAcceptanceReconciler._component_ids(component))
                continue
            children, child_removed, child_ids = ArchitectureAcceptanceReconciler._remove_component(
                component.children,
                component_id,
            )
            removed_ids.update(child_ids)
            if child_removed is not None:
                removed = child_removed
                updated.append(component.model_copy(update={"children": children}, deep=True))
            else:
                updated.append(component.model_copy(deep=True))
        return updated, removed, removed_ids

    @staticmethod
    def _update_component(
        nodes: list[Component],
        component_id: str,
        changes: dict,
    ) -> tuple[list[Component], Component | None, Component | None]:
        before: Component | None = None
        after: Component | None = None
        updated: list[Component] = []
        for component in nodes:
            if component.id == component_id:
                before = component
                candidate = Component.model_validate(
                    {**component.model_dump(mode="python"), **changes}
                )
                after = candidate
                updated.append(candidate)
                continue
            children, child_before, child_after = ArchitectureAcceptanceReconciler._update_component(
                component.children,
                component_id,
                changes,
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

    @staticmethod
    def _rescope_component_reference(text: str, old_name: str, new_name: str) -> str:
        if not text or not old_name or not new_name:
            return text
        if old_name in text:
            return text.replace(old_name, new_name)
        generic_words = {
            "application", "backend", "channel", "client", "database",
            "frontend", "platform", "service", "system", "web",
        }
        for token in old_name.replace("/", " ").replace("-", " ").split():
            cleaned = token.strip("()[]{}.,:;")
            if len(cleaned) < 4 or cleaned.lower() in generic_words:
                continue
            if cleaned in text:
                return text.replace(cleaned, new_name, 1)
        return text

    @staticmethod
    def _normalize_replacement(component: Component, change: dict) -> Component:
        supported_top = {
            "operation", "component_id", "replacement", "new_id", "new_name",
            "new_type", "new_responsibility", "new_status", "new_kind",
        }
        unsupported = set(change).difference(supported_top)
        if unsupported:
            raise ValueError(
                "replace_component contains unsupported fields: "
                + ", ".join(sorted(unsupported))
            )

        raw = change.get("replacement")
        if raw is not None:
            if not isinstance(raw, dict):
                raise ValueError("replace_component replacement must be an object")
            allowed_replacement = {"id", "name", "type", "responsibility", "status", "kind"}
            unsupported_replacement = set(raw).difference(allowed_replacement)
            if unsupported_replacement:
                raise ValueError(
                    "replace_component replacement contains unsupported fields: "
                    + ", ".join(sorted(unsupported_replacement))
                )
            name = str(raw.get("name", "")).strip()
            if not name:
                raise ValueError("replace_component replacement requires a non-empty name")
            values = component.model_dump(mode="python")
            values.update(
                {
                    "id": str(raw.get("id") or component.id).strip(),
                    "name": name,
                    "type": str(raw.get("type") or component.type).strip(),
                    "responsibility": str(raw.get("responsibility") or component.responsibility).strip(),
                    "status": str(raw.get("status") or component.status).strip(),
                    "kind": raw.get("kind") or component.kind,
                }
            )
            return Component.model_validate(values)

        name = str(change.get("new_name", "")).strip()
        if not name:
            raise ValueError("replace_component requires a non-empty new_name")
        new_type = change.get("new_type")
        new_responsibility = change.get("new_responsibility")
        new_status = change.get("new_status")
        values = component.model_dump(mode="python")
        values.update(
            {
                "id": str(change.get("new_id") or component.id).strip(),
                "name": name,
                "type": str(new_type).strip() if new_type is not None and str(new_type).strip() else component.type,
                "responsibility": (
                    str(new_responsibility).strip()
                    if new_responsibility is not None and str(new_responsibility).strip()
                    else component.responsibility
                ),
                "status": (
                    str(new_status).strip()
                    if new_status is not None and str(new_status).strip()
                    else component.status
                ),
                "kind": (
                    ArchitectureNodeKind(str(change["new_kind"]))
                    if change.get("new_kind")
                    else component.kind
                ),
            }
        )
        return Component.model_validate(values)

    def build_plan(
        self,
        *,
        architecture: Architecture,
        proposal: ArchitectureChangeProposal,
        tasks: list[Task],
    ) -> AcceptanceReconciliationPlan:
        if not proposal.proposed_changes:
            raise ValueError("accepted architecture proposal requires at least one proposed change")

        original_ids = architecture.component_ids()
        next_architecture = architecture.model_copy(deep=True)
        replacements: dict[str, tuple[Component, Component]] = {}
        removed_ids: set[str] = set()
        updated_ids: set[str] = set()
        directly_changed: set[str] = set()
        relationships_replaced = False

        for raw_change in proposal.proposed_changes:
            if not isinstance(raw_change, dict):
                raise ValueError("proposed architecture changes must be objects")
            change = dict(raw_change)
            operation = str(change.get("operation", "")).strip()

            if operation == "replace_component":
                component_id = str(change.get("component_id", "")).strip()
                if not component_id:
                    raise ValueError("replace_component requires component_id")
                if component_id in directly_changed:
                    raise ValueError(f"proposal changes component more than once: {component_id}")
                if component_id not in proposal.affected_components:
                    raise ValueError("changed component must be included in proposal affected_components")
                before = next_architecture.find_component(component_id)
                if before is None:
                    raise ValueError(f"affected component not found: {component_id}")
                after = self._normalize_replacement(before, change)
                if before == after:
                    raise ValueError(f"proposal replacement is a no-op for component: {component_id}")
                components, found_before = self._replace_component(
                    next_architecture.components,
                    component_id,
                    after,
                )
                if found_before is None:
                    raise ValueError(f"affected component not found: {component_id}")
                next_architecture.components = components
                replacements[component_id] = (before, after)
                directly_changed.add(component_id)
                continue

            if operation == "remove_component":
                supported = {"operation", "component_id"}
                unsupported = set(change).difference(supported)
                if unsupported:
                    raise ValueError(
                        "remove_component contains unsupported fields: "
                        + ", ".join(sorted(unsupported))
                    )
                component_id = str(change.get("component_id", "")).strip()
                if not component_id:
                    raise ValueError("remove_component requires component_id")
                if component_id in directly_changed:
                    raise ValueError(f"proposal changes component more than once: {component_id}")
                if component_id not in proposal.affected_components:
                    raise ValueError("changed component must be included in proposal affected_components")
                components, removed, subtree_ids = self._remove_component(
                    next_architecture.components,
                    component_id,
                )
                if removed is None:
                    raise ValueError(f"affected component not found: {component_id}")
                next_architecture.components = components
                removed_ids.update(subtree_ids)
                directly_changed.add(component_id)
                continue

            if operation == "update_component":
                supported = {"operation", "component_id", "changes"}
                unsupported = set(change).difference(supported)
                if unsupported:
                    raise ValueError(
                        "update_component contains unsupported fields: "
                        + ", ".join(sorted(unsupported))
                    )
                component_id = str(change.get("component_id", "")).strip()
                if not component_id:
                    raise ValueError("update_component requires component_id")
                if component_id in directly_changed:
                    raise ValueError(f"proposal changes component more than once: {component_id}")
                if component_id not in proposal.affected_components:
                    raise ValueError("changed component must be included in proposal affected_components")
                changes = change.get("changes")
                if not isinstance(changes, dict) or not changes:
                    raise ValueError("update_component requires a non-empty changes object")
                allowed = {"name", "type", "responsibility", "status", "kind"}
                unsupported_changes = set(changes).difference(allowed)
                if unsupported_changes:
                    raise ValueError(
                        "update_component contains unsupported fields: "
                        + ", ".join(sorted(unsupported_changes))
                    )
                components, before, after = self._update_component(
                    next_architecture.components,
                    component_id,
                    changes,
                )
                if before is None or after is None:
                    raise ValueError(f"affected component not found: {component_id}")
                if before == after:
                    raise ValueError(f"proposal update is a no-op for component: {component_id}")
                next_architecture.components = components
                updated_ids.add(component_id)
                directly_changed.add(component_id)
                continue

            if operation == "replace_relationships":
                supported = {"operation", "changes", "relationships"}
                unsupported = set(change).difference(supported)
                if unsupported:
                    raise ValueError(
                        "replace_relationships contains unsupported fields: "
                        + ", ".join(sorted(unsupported))
                    )
                raw_relationships = change.get("changes", change.get("relationships"))
                if not isinstance(raw_relationships, list):
                    raise ValueError("replace_relationships requires a changes list")
                relationships: list[Relationship] = []
                for item in raw_relationships:
                    if not isinstance(item, dict):
                        raise ValueError("relationship changes must be objects")
                    relationship_type = item.get("relationship_type", item.get("type"))
                    if not str(relationship_type or "").strip():
                        raise ValueError("relationship change requires type or relationship_type")
                    relationships.append(
                        Relationship.model_validate(
                            {
                                "source": item.get("source"),
                                "target": item.get("target"),
                                "relationship_type": relationship_type,
                                "description": item.get("description", ""),
                            }
                        )
                    )
                next_architecture.relationships = relationships
                relationships_replaced = True
                continue

            raise ValueError(f"unsupported architecture change operation: {operation or '<missing>'}")

        # Validate additional impacted components after direct operations so a bad
        # direct target preserves the more precise historical error contract.
        unknown_affected = set(proposal.affected_components).difference(original_ids)
        if unknown_affected:
            raise ValueError(
                "proposal references unknown affected components: "
                + ", ".join(sorted(unknown_affected))
            )

        replacement_id_map = {
            old_id: after.id for old_id, (_before, after) in replacements.items()
        }
        if not relationships_replaced and (replacement_id_map or removed_ids):
            remapped: list[Relationship] = []
            for relationship in next_architecture.relationships:
                source = replacement_id_map.get(relationship.source, relationship.source)
                target = replacement_id_map.get(relationship.target, relationship.target)
                if source in removed_ids or target in removed_ids:
                    continue
                remapped.append(
                    relationship.model_copy(update={"source": source, "target": target})
                )
            next_architecture.relationships = remapped

        next_architecture.version = architecture.version + 1
        next_architecture.decisions.append(
            f"Accepted proposal {proposal.id}: {proposal.reason}"
        )
        next_architecture = Architecture.model_validate(
            next_architecture.model_dump(mode="python")
        )

        now = datetime.now(timezone.utc)
        affected = set(proposal.affected_components)
        updated_tasks: list[Task] = []
        rescaled_replacement_ids: set[str] = set()

        for task in tasks:
            related = task.related_component
            if related is None:
                continue

            replacement = replacements.get(related)
            if replacement is not None:
                before, after = replacement
                if after.id != before.id:
                    values = {
                        "related_component": after.id,
                        "title": self._rescope_component_reference(task.title, before.name, after.name),
                        "description": self._rescope_component_reference(task.description, before.name, after.name),
                        "updated_at": now,
                    }
                    if task.status != TaskStatus.DONE:
                        values["status"] = TaskStatus.TODO
                        note = "Re-scoped to the accepted replacement architecture component."
                        description = str(values["description"]).strip()
                        if note not in description:
                            values["description"] = f"{description} {note}".strip()
                    updated_tasks.append(task.model_copy(update=values))
                    rescaled_replacement_ids.add(related)
                    continue

                if task.status == TaskStatus.DONE:
                    continue
                aligned_title = self._migration_task_title(before, after)
                if task.title.strip().casefold() == aligned_title.strip().casefold():
                    continue
                note = (
                    f"Accepted architecture changed {before.name} to {after.name}; "
                    "re-evaluate this task before continuing."
                )
                updated_tasks.append(self._append_reconciliation_note(task, note, now))
                continue

            if related in removed_ids:
                values = {"related_component": None, "updated_at": now}
                if task.status != TaskStatus.DONE:
                    values["status"] = TaskStatus.BLOCKED
                    note = "Re-evaluate because its architecture component was removed."
                    description = task.description.strip()
                    if note not in description:
                        values["description"] = f"{description} {note}".strip()
                updated_tasks.append(task.model_copy(update=values))
                continue

            if related in updated_ids:
                # Responsibility/name updates preserve existing executable work. They do
                # not by themselves invalidate task status.
                continue

            if related in affected and task.status != TaskStatus.DONE:
                note = (
                    "Accepted architecture change impacts this component; "
                    "re-evaluate this task before continuing."
                )
                updated_tasks.append(self._append_reconciliation_note(task, note, now))

        created_tasks: list[Task] = []
        effective_tasks = [
            next((updated for updated in updated_tasks if updated.id == task.id), task)
            for task in tasks
        ]
        existing_task_keys = {
            (task.related_component, task.title.strip().casefold())
            for task in effective_tasks
        }

        for old_id, (before, after) in replacements.items():
            if after.id != before.id and old_id in rescaled_replacement_ids:
                continue
            title = self._migration_task_title(before, after)
            target_component_id = after.id
            task_key = (target_component_id, title.strip().casefold())
            if task_key in existing_task_keys:
                continue
            responsibility = after.responsibility.strip()
            description = (
                f"Implement the accepted architecture change replacing {before.name} "
                f"with {after.name} for component {target_component_id}."
            )
            if responsibility:
                description += f" Accepted responsibility: {responsibility}."
            acceptance_criteria = [
                f"{after.name} is implemented as the accepted replacement for {before.name}."
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
                    related_component=target_component_id,
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
