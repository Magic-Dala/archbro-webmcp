from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DriftClassification(StrEnum):
    ALIGNED = "ALIGNED"
    IMPLEMENTATION_ISSUE = "IMPLEMENTATION_ISSUE"
    ARCHITECTURE_DRIFT = "ARCHITECTURE_DRIFT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DriftRecommendedAction(StrEnum):
    NO_ACTION = "NO_ACTION"
    UPDATE_TASK = "UPDATE_TASK"
    KEEP_CURRENT = "KEEP_CURRENT"
    PROPOSE_ARCHITECTURE_CHANGE = "PROPOSE_ARCHITECTURE_CHANGE"


class DriftEvaluation(BaseModel):
    """Structured assessment of whether observed reality still fits accepted architecture."""

    classification: DriftClassification
    summary: str
    evidence: list[str] = Field(default_factory=list, max_length=5)
    affected_components: list[str] = Field(default_factory=list)
    affected_tasks: list[str] = Field(default_factory=list)
    architecture_change_required: bool = False
    recommended_action: DriftRecommendedAction = DriftRecommendedAction.NO_ACTION

    @model_validator(mode="after")
    def validate_semantics(self) -> "DriftEvaluation":
        if self.classification == DriftClassification.ARCHITECTURE_DRIFT:
            if not self.architecture_change_required:
                raise ValueError("ARCHITECTURE_DRIFT requires architecture_change_required=true")
            if self.recommended_action != DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE:
                raise ValueError("ARCHITECTURE_DRIFT requires PROPOSE_ARCHITECTURE_CHANGE")
            if not self.evidence:
                raise ValueError("ARCHITECTURE_DRIFT requires explicit evidence")
        elif self.architecture_change_required:
            raise ValueError("only ARCHITECTURE_DRIFT may require an architecture change")
        elif self.recommended_action == DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE:
            raise ValueError("only ARCHITECTURE_DRIFT may recommend an architecture proposal")

        if self.classification == DriftClassification.ALIGNED and self.recommended_action not in {
            DriftRecommendedAction.NO_ACTION,
            DriftRecommendedAction.UPDATE_TASK,
        }:
            raise ValueError("ALIGNED may only recommend NO_ACTION or UPDATE_TASK")

        if self.classification == DriftClassification.IMPLEMENTATION_ISSUE and self.recommended_action not in {
            DriftRecommendedAction.NO_ACTION,
            DriftRecommendedAction.UPDATE_TASK,
            DriftRecommendedAction.KEEP_CURRENT,
        }:
            raise ValueError("IMPLEMENTATION_ISSUE cannot recommend an architecture proposal")

        if self.classification == DriftClassification.INSUFFICIENT_EVIDENCE and self.recommended_action not in {
            DriftRecommendedAction.NO_ACTION,
            DriftRecommendedAction.KEEP_CURRENT,
        }:
            raise ValueError("INSUFFICIENT_EVIDENCE must preserve current architecture")
        return self
