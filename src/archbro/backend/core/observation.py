from __future__ import annotations

from dataclasses import dataclass, field

from archbro.backend.core.contracts import (
    Architecture,
    ArchitectureChangeProposal,
    Project,
    Task,
)


class ObservationInProgressError(RuntimeError):
    """Raised when the same durable observation is already being evaluated."""


class ObservationRejectedError(ValueError):
    """Raised when a durable observation identity is permanently invalid.

    This is distinct from transient observation contention and from unrelated
    persistence ``ValueError`` failures. Callers may safely treat it as a terminal
    delivery rejection: retrying the same identity and payload cannot succeed.
    """


@dataclass(slots=True)
class ObservationMutationPlan:
    """Fully materialized domain mutations plus the state they were planned from."""

    project: Project | None = None
    architecture: Architecture | None = None
    tasks: list[Task] = field(default_factory=list)
    proposals: list[ArchitectureChangeProposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    expected_project_updated_at: str | None = None
    expected_architecture_version: int | None = None
    expected_task_updated_at: dict[str, str] = field(default_factory=dict)

    @property
    def proposal_ids(self) -> list[str]:
        return [proposal.id for proposal in self.proposals]
