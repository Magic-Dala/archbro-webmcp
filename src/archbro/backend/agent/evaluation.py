from __future__ import annotations

from archbro.backend.core.contracts import (
    AgentActionType,
    AgentDecision,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    ProjectContext,
)
from archbro.backend.core.evaluation import DriftClassification


class DriftPolicy:
    """Deterministic gate between model reasoning and product-state mutation."""

    @staticmethod
    def validate(context: ProjectContext, decision: AgentDecision) -> None:
        if context.architecture.version <= 0:
            return

        evaluation = decision.evaluation
        if evaluation is None:
            raise ValueError("normal agent decisions require a DriftEvaluation")

        known_components = context.architecture.component_ids()
        unknown_components = set(evaluation.affected_components).difference(known_components)
        if unknown_components:
            raise ValueError(
                "DriftEvaluation references unknown architecture components: "
                + ", ".join(sorted(unknown_components))
            )

        known_tasks = {task.id for task in context.tasks}
        unknown_tasks = set(evaluation.affected_tasks).difference(known_tasks)
        if unknown_tasks:
            raise ValueError(
                "DriftEvaluation references unknown tasks: "
                + ", ".join(sorted(unknown_tasks))
            )

        proposal_actions = [
            action
            for action in decision.actions
            if action.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE
        ]

        if evaluation.classification == DriftClassification.ARCHITECTURE_DRIFT:
            if len(proposal_actions) != 1:
                raise ValueError("ARCHITECTURE_DRIFT requires exactly one architecture proposal")

            # Proposed architecture is not accepted reality yet. M5 owns task
            # reconciliation after human acceptance, so normal M4 evaluation may
            # update existing observed task state but must not pre-create work for
            # an unaccepted architecture or relink tasks to it.
            if any(action.type == AgentActionType.CREATE_TASK for action in decision.actions):
                raise ValueError("ARCHITECTURE_DRIFT may not create tasks before human acceptance")
            for action in decision.actions:
                if action.type == AgentActionType.UPDATE_TASK and "related_component" in action.payload["changes"]:
                    raise ValueError("ARCHITECTURE_DRIFT may not relink tasks before human acceptance")

            proposal = ArchitectureChangeProposal.model_validate(proposal_actions[0].payload["proposal"])
            if set(proposal.affected_components) != set(evaluation.affected_components):
                raise ValueError(
                    "proposal affected_components must match DriftEvaluation affected_components"
                )
            if not proposal.proposed_changes:
                raise ValueError("ARCHITECTURE_DRIFT proposal requires at least one executable proposed change")
            for change in proposal.proposed_changes:
                operation = str(change.get("operation", "")).strip()
                if operation != "replace_component":
                    raise ValueError(f"unsupported architecture change operation: {operation or '<missing>'}")
                component_id = str(change.get("component_id", "")).strip()
                if component_id not in known_components:
                    raise ValueError(f"proposed change references unknown component: {component_id or '<missing>'}")
                if component_id not in proposal.affected_components:
                    raise ValueError("changed component must be included in proposal affected_components")
                if not str(change.get("new_name", "")).strip():
                    raise ValueError("replace_component requires a non-empty new_name")
                if change.get("new_kind"):
                    ArchitectureNodeKind(str(change["new_kind"]))
        elif proposal_actions:
            raise ValueError(
                f"{evaluation.classification.value} may not propose an architecture change"
            )
