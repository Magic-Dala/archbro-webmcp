from __future__ import annotations

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    Architecture,
    ArchitectureNodeKind,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    ProjectContext,
    ProjectEvent,
    ProjectEventType,
    Relationship,
    TaskProposal,
)
from archbro.backend.core.evaluation import DriftEvaluation
from archbro.backend.llm.provider import GoalConversationMessage, GoalDraft, ModelProvider

load_dotenv()


DEFAULT_GEMINI_CHAIN = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

DEFAULT_GEMINI_GOAL_CHAIN = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
)

DEFAULT_GEMINI_ROUTINE_CHAIN = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
)


class GeminiArchitectureProposalWire(BaseModel):
    """Provider-only proposal payload. Server-owned identity/state fields are added deterministically."""

    reason: str
    evidence: list[str] = Field(min_length=1, max_length=5)
    observed_change: str
    affected_components: list[str] = Field(default_factory=list, max_length=7)
    proposed_changes: list[dict[str, object]] = Field(default_factory=list, max_length=7)
    impact: str
    recommended_option: ArchitectureOption


class GeminiComponentWire(BaseModel):
    """Flat provider-only architecture node.

    The product domain keeps recursive Component.children, but recursive Pydantic
    schemas are not safe to hand directly to Strands/Google structured output.
    parent_id encodes the hierarchy without recursion and is rebuilt
    deterministically after validation.
    """

    id: str
    name: str
    type: str
    responsibility: str
    status: str = "PLANNED"
    kind: ArchitectureNodeKind = ArchitectureNodeKind.SYSTEM
    parent_id: str | None = None


class GeminiArchitectureWire(BaseModel):
    """Non-recursive provider wire for a hierarchical Architecture v1."""

    version: int = 1
    summary: str = ""
    components: list[GeminiComponentWire] = Field(min_length=3, max_length=40)
    relationships: list[Relationship] = Field(default_factory=list, max_length=12)
    decisions: list[str] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=3)
    risks: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_flat_hierarchy(self) -> "GeminiArchitectureWire":
        if self.version != 1:
            raise ValueError("bootstrap architecture.version must be 1")

        by_id = {component.id: component for component in self.components}
        if len(by_id) != len(self.components):
            raise ValueError("bootstrap architecture node ids must be unique")

        top_level = [component for component in self.components if component.parent_id is None]
        if not 3 <= len(top_level) <= 8:
            raise ValueError("bootstrap requires 3-8 top-level components")

        child_counts: dict[str, int] = {}
        depths: dict[str, int] = {}

        for component in self.components:
            if component.parent_id is not None:
                if component.parent_id == component.id:
                    raise ValueError("architecture node cannot parent itself")
                if component.parent_id not in by_id:
                    raise ValueError(f"unknown parent_id for architecture node: {component.parent_id}")
                child_counts[component.parent_id] = child_counts.get(component.parent_id, 0) + 1

            depth = 1
            cursor = component
            seen = {component.id}
            while cursor.parent_id is not None:
                if cursor.parent_id in seen:
                    raise ValueError("architecture hierarchy cannot contain cycles")
                seen.add(cursor.parent_id)
                cursor = by_id[cursor.parent_id]
                depth += 1
                if depth > 3:
                    raise ValueError("architecture depth is capped at 3 levels")
            depths[component.id] = depth

        for parent_id, count in child_counts.items():
            parent_depth = depths[parent_id]
            if parent_depth == 1 and count > 7:
                raise ValueError("top-level architecture nodes allow at most 7 children")
            if parent_depth == 2 and count > 6:
                raise ValueError("level-2 architecture nodes allow at most 6 children")
            if parent_depth >= 3 and count:
                raise ValueError("level-3 architecture nodes cannot have children")

        component_ids = set(by_id)
        if any(rel.source not in component_ids or rel.target not in component_ids for rel in self.relationships):
            raise ValueError("bootstrap relationships must reference component ids")
        return self

    def component_ids(self) -> set[str]:
        return {component.id for component in self.components}

    def to_domain(self) -> Architecture:
        nodes = {
            wire.id: Component(
                id=wire.id,
                name=wire.name,
                type=wire.type,
                responsibility=wire.responsibility,
                status=wire.status,
                kind=wire.kind,
                children=[],
            )
            for wire in self.components
        }
        roots: list[Component] = []
        for wire in self.components:
            node = nodes[wire.id]
            if wire.parent_id is None:
                roots.append(node)
            else:
                nodes[wire.parent_id].children.append(node)
        return Architecture(
            version=self.version,
            summary=self.summary,
            components=roots,
            relationships=self.relationships,
            decisions=self.decisions,
            assumptions=self.assumptions,
            risks=self.risks,
        )


class GeminiDecisionWire(BaseModel):
    """Provider-only structured output. It is converted into the shared AgentDecision contract."""

    summary: str
    evaluation: DriftEvaluation
    architecture_review_required: bool = False
    architecture_proposal: GeminiArchitectureProposalWire | None = None
    actions: list[AgentAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_proposal_shape(self) -> "GeminiDecisionWire":
        if self.architecture_review_required != (self.architecture_proposal is not None):
            raise ValueError("architecture_review_required must match architecture_proposal presence")
        return self


class GeminiBootstrapWire(BaseModel):
    """Small provider-only schema for initial V0 architecture generation."""

    summary: str
    architecture: GeminiArchitectureWire
    tasks: list[TaskProposal] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_small_bootstrap(self) -> "GeminiBootstrapWire":
        component_ids = self.architecture.component_ids()
        if any(task.related_component and task.related_component not in component_ids for task in self.tasks):
            raise ValueError("bootstrap task.related_component must reference a component id")
        return self


class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, model_id: str = "gemini-3.7-flash") -> None:
        self.model_id = model_id
        self.last_model_id = model_id
        self.fallback_model_ids = self._load_fallback_models(model_id)
        self.goal_model_id = os.getenv("GEMINI_GOAL_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
        self.goal_fallback_model_ids = self._load_goal_fallback_models(self.goal_model_id)
        self.goal_model_timeout_seconds = float(os.getenv("GEMINI_GOAL_MODEL_TIMEOUT_SECONDS", "8"))
        if self.goal_model_timeout_seconds <= 0:
            raise ValueError("GEMINI_GOAL_MODEL_TIMEOUT_SECONDS must be greater than zero")
        self.routine_model_id = os.getenv("GEMINI_ROUTINE_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
        self.routine_fallback_model_ids = self._load_routine_fallback_models(self.routine_model_id)
        self.routine_model_timeout_seconds = float(os.getenv("GEMINI_ROUTINE_MODEL_TIMEOUT_SECONDS", "8"))
        if self.routine_model_timeout_seconds <= 0:
            raise ValueError("GEMINI_ROUTINE_MODEL_TIMEOUT_SECONDS must be greater than zero")
        self.architecture_model_timeout_seconds = float(os.getenv("GEMINI_ARCHITECTURE_MODEL_TIMEOUT_SECONDS", "12"))
        self.architecture_total_timeout_seconds = float(os.getenv("GEMINI_ARCHITECTURE_TOTAL_TIMEOUT_SECONDS", "36"))
        if self.architecture_model_timeout_seconds <= 0 or self.architecture_total_timeout_seconds <= 0:
            raise ValueError("Gemini architecture timeouts must be greater than zero")
        bootstrap_fallbacks = os.getenv(
            "GEMINI_BOOTSTRAP_FALLBACK_MODELS",
            "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.5-flash",
        )
        self.bootstrap_fallback_model_ids = tuple(
            candidate
            for candidate in (item.strip() for item in bootstrap_fallbacks.split(","))
            if candidate and candidate != self.model_id
        )
        self._api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        # Strands Agent instances are invocation-scoped. Reusing one Agent across HTTP
        # requests raises ConcurrencyException because concurrent invocations are unsupported.

    @staticmethod
    def _load_fallback_models(primary_model_id: str) -> tuple[str, ...]:
        configured = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
        legacy = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()

        if configured:
            requested = [item.strip() for item in configured.split(",") if item.strip()]
        elif legacy:
            requested = [legacy]
        else:
            requested = list(DEFAULT_GEMINI_CHAIN)

        deduped: list[str] = []
        for candidate in requested:
            if candidate != primary_model_id and candidate not in deduped:
                deduped.append(candidate)
        return tuple(deduped)

    @staticmethod
    def _load_goal_fallback_models(primary_model_id: str) -> tuple[str, ...]:
        configured = os.getenv("GEMINI_GOAL_FALLBACK_MODELS", "").strip()
        requested = (
            [item.strip() for item in configured.split(",") if item.strip()]
            if configured
            else list(DEFAULT_GEMINI_GOAL_CHAIN)
        )
        deduped: list[str] = []
        for candidate in requested:
            if candidate != primary_model_id and candidate not in deduped:
                deduped.append(candidate)
        return tuple(deduped)

    @staticmethod
    def _load_routine_fallback_models(primary_model_id: str) -> tuple[str, ...]:
        configured = os.getenv("GEMINI_ROUTINE_FALLBACK_MODELS", "").strip()
        requested = (
            [item.strip() for item in configured.split(",") if item.strip()]
            if configured
            else list(DEFAULT_GEMINI_ROUTINE_CHAIN)
        )
        deduped: list[str] = []
        for candidate in requested:
            if candidate != primary_model_id and candidate not in deduped:
                deduped.append(candidate)
        return tuple(deduped)

    @property
    def model_chain(self) -> tuple[str, ...]:
        return (self.model_id, *self.fallback_model_ids)

    @property
    def goal_model_chain(self) -> tuple[str, ...]:
        return (self.goal_model_id, *self.goal_fallback_model_ids)

    @property
    def routine_model_chain(self) -> tuple[str, ...]:
        return (self.routine_model_id, *self.routine_fallback_model_ids)

    @property
    def bootstrap_model_chain(self) -> tuple[str, ...]:
        deduped: list[str] = []
        for candidate in (self.model_id, *self.bootstrap_fallback_model_ids):
            if candidate not in deduped:
                deduped.append(candidate)
        return tuple(deduped)

    def _build_agent(self, model_id: str):
        from google.genai import types as genai_types
        from strands import Agent
        from strands.models.gemini import GeminiModel

        http_timeout_ms = int(os.getenv("GEMINI_HTTP_TIMEOUT_MS", "12000"))
        if http_timeout_ms <= 0:
            raise ValueError("GEMINI_HTTP_TIMEOUT_MS must be greater than zero")
        model = GeminiModel(
            client_args={
                "api_key": self._api_key,
                "http_options": genai_types.HttpOptions(
                    timeout=http_timeout_ms,
                    retry_options=genai_types.HttpRetryOptions(attempts=1),
                ),
            },
            model_id=model_id,
            params={"temperature": 0.1, "max_output_tokens": 4096},
        )
        return Agent(model=model, callback_handler=None)

    def _agent_for(self, model_id: str):
        return self._build_agent(model_id)

    @staticmethod
    def _is_temporary_unavailable(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            text = str(current).lower()
            if "503" in text and ("unavailable" in text or "high demand" in text):
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _invoke(self, model_id: str, prompt: str) -> GeminiDecisionWire:
        result = await self._agent_for(model_id).invoke_async(prompt, structured_output_model=GeminiDecisionWire)
        if result.structured_output is None:
            raise RuntimeError("Strands returned no structured GeminiDecisionWire")
        return GeminiDecisionWire.model_validate(result.structured_output)

    async def _invoke_goal(self, model_id: str, prompt: str) -> GoalDraft:
        result = await self._agent_for(model_id).invoke_async(prompt, structured_output_model=GoalDraft)
        if result.structured_output is None:
            raise RuntimeError("Strands returned no structured GoalDraft")
        return GoalDraft.model_validate(result.structured_output)

    async def _invoke_bootstrap(self, model_id: str, prompt: str) -> GeminiBootstrapWire:
        result = await self._agent_for(model_id).invoke_async(prompt, structured_output_model=GeminiBootstrapWire)
        if result.structured_output is None:
            raise RuntimeError("Strands returned no structured GeminiBootstrapWire")
        return GeminiBootstrapWire.model_validate(result.structured_output)

    @staticmethod
    def _bootstrap_to_domain_decision(wire: GeminiBootstrapWire) -> AgentDecision:
        architecture = wire.architecture.to_domain()
        actions = [
            AgentAction(
                type=AgentActionType.ADD_PROJECT_NOTE,
                payload={"note": "INITIAL_ARCHITECTURE:" + architecture.model_dump_json()},
            )
        ]
        actions.extend(
            AgentAction(
                type=AgentActionType.CREATE_TASK,
                payload={"task": task.model_dump(mode="json")},
            )
            for task in wire.tasks
        )
        return AgentDecision(summary=wire.summary, actions=actions, architecture_review_required=False)

    async def draft_goal(
        self,
        *,
        messages: list[GoalConversationMessage],
        current_goal: str = "",
    ) -> GoalDraft:
        baseline = current_goal.strip()
        has_user_ask = any(message.role == "user" and message.content.strip() for message in messages)
        if not has_user_ask and not baseline:
            raise ValueError("a current Goal or at least one user Ask message is required")

        conversation = [message.model_dump(mode="json") for message in messages]
        prompt = (
            "You are the project-briefing stage of Archbro. The project does not exist yet. "
            "The user can work in TWO equivalent ways: directly edit the Goal draft, or use Ask to refine it. "
            "Your job is to MERGE the current Goal and the Ask conversation into one canonical Goal / Project Brief. "
            "Do NOT design an architecture, create tasks, choose infrastructure without evidence, or pretend project state already exists.\n\n"
            "NON-DESTRUCTIVE GOAL MERGE CONTRACT - mandatory:\n"
            "- CURRENT GOAL is an authoritative baseline written or previously accepted by the user.\n"
            "- Preserve all still-compatible requirements, constraints, outcomes, technologies, milestones, and scope already present in CURRENT GOAL.\n"
            "- Treat new Ask messages as additions, refinements, corrections, or explicit changes to that baseline.\n"
            "- Never replace the whole Goal merely because the latest Ask is shorter or discusses one detail.\n"
            "- Never return an empty Goal when CURRENT GOAL is non-empty.\n"
            "- Remove or replace an existing Goal requirement only when the user's Ask explicitly says that requirement changed, is no longer wanted, or should be rewritten.\n"
            "- If the Ask conflicts with CURRENT GOAL, resolve only the conflicting part and preserve unrelated Goal content.\n"
            "- The resulting goal field must be self-contained; a later architecture agent should not need the conversation transcript.\n\n"
            "For every turn return a GoalDraft. Set ready=true when the product outcome and first usable milestone are sufficiently clear. "
            "Explicit technical requirements or constraints must be preserved when the user gave them. "
            "Do not force the user to choose technologies if they intentionally leave those choices to the agent. "
            "If something material is still missing, set ready=false, list only important missing_information, and ask one focused natural follow-up in assistant_message. "
            "If ready=true, assistant_message should briefly summarize what changed in the Goal and say it may still be refined. "
            "suggested_project_name should be short and derived from the combined Goal and Ask.\n\n"
            "CURRENT GOAL:\n"
            + (baseline or "<empty>")
            + "\n\nASK CONVERSATION JSON:\n"
            + json.dumps(conversation, ensure_ascii=False)
        )

        unavailable: list[str] = []
        timed_out: list[str] = []
        last_unavailable: Exception | None = None
        for candidate in self.goal_model_chain:
            self.last_model_id = candidate
            try:
                draft = await asyncio.wait_for(
                    self._invoke_goal(candidate, prompt),
                    timeout=self.goal_model_timeout_seconds,
                )
                if baseline and not draft.goal.strip():
                    raise ValueError("Goal merge attempted to clear a non-empty current Goal")
                return draft
            except TimeoutError as exc:
                timed_out.append(candidate)
                last_unavailable = exc
                continue
            except Exception as exc:
                if not self._is_temporary_unavailable(exc):
                    raise
                unavailable.append(candidate)
                last_unavailable = exc

        details: list[str] = []
        if timed_out:
            details.append("timed out: " + ", ".join(timed_out))
        if unavailable:
            details.append("503 unavailable: " + ", ".join(unavailable))
        reason = "; ".join(details) or "no model completed"
        raise RuntimeError(
            f"Goal drafting could not complete within the provider deadline ({reason}). "
            "The current Goal and Ask were not changed; retry the Ask."
        ) from last_unavailable

    @staticmethod
    def _to_domain_decision(
        wire: GeminiDecisionWire,
        *,
        event: ProjectEvent,
        context: ProjectContext,
    ) -> AgentDecision:
        if any(action.type == AgentActionType.PROPOSE_ARCHITECTURE_CHANGE for action in wire.actions):
            raise ValueError("use architecture_proposal provider field instead of a free-form proposal action")

        actions = list(wire.actions)
        if wire.architecture_proposal is not None:
            proposal = ArchitectureChangeProposal(
                project_id=context.project.id,
                **wire.architecture_proposal.model_dump(mode="json"),
            )
            actions.append(
                AgentAction(
                    type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
                    payload={"proposal": proposal.model_dump(mode="json")},
                )
            )
        return AgentDecision(
            summary=wire.summary,
            actions=actions,
            architecture_review_required=wire.architecture_review_required,
            evaluation=wire.evaluation,
        )

    async def generate(self, *, event: ProjectEvent, context: ProjectContext, system_prompt: str) -> AgentDecision:
        is_routine_update = event.type == ProjectEventType.TASK_UPDATED
        is_bootstrap = (
            context.architecture.version == 0
            and event.type == ProjectEventType.USER_MESSAGE
            and event.payload.get("intent") == "INITIAL_ARCHITECTURE"
        )

        if is_bootstrap:
            prompt = (
                "Create the smallest useful V0 software architecture from this confirmed project brief. "
                "This is initial setup, not an architecture change. Return architecture version 1. "
                "Use 3-8 TOP-LEVEL components and at most 12 relationships. Create 3-6 concrete human implementation tasks. "
                "The provider schema is intentionally FLAT to avoid recursive structured-output schemas: every architecture component has a parent_id field. Use parent_id=null for top-level system boundaries and set parent_id to another component id for child/detail nodes. Do not output nested children arrays. The server rebuilds the validated hierarchy deterministically. "
                "The top level is a human overview: only meaningful system boundaries belong there. Use child nodes for component detail when the parent contains independently meaningful UI modules, services, tools, state stores, or infrastructure. Children are optional; do not decompose a simple component just to make the graph look detailed. "
                "Architecture depth is capped at 3 levels: top-level system overview, component architecture, then optional implementation detail. A top-level component may have at most 7 children and a level-2 child may have at most 6 children. Keep the total architecture comfortably below 40 nodes, ideally 8-20 for a multi-capability V0. "
                "Set Component.kind to one of SYSTEM, UI, SERVICE, AGENT, TOOL, DATA_STORE, STATE, EXTERNAL_SERVICE, INFRASTRUCTURE. Keep Component.type domain/technology-specific. "
                "Do not model runtime verbs such as Understand, Evaluate, Decide, or Explain as components unless they are backed by an actual tool/service boundary. Runtime steps belong in a runtime flow, not the component hierarchy. "
                "Decompose by meaningful system boundaries, not by an arbitrary component count. A capability deserves its own component when it has a distinct responsibility, owns state, crosses an external/service integration boundary, or can be implemented as an independent human workstream. "
                "When the confirmed Goal explicitly contains several materially different capabilities, do not collapse them into one generic Backend or Agent Core. In particular, keep user experience, agent orchestration, domain/API behavior, persistence/state, and external search/recommendation/data integrations separate when the Goal actually requires those boundaries. "
                "For a multi-capability Goal, do not leave the entire architecture flat when a top-level boundary clearly contains two or more independently implementable capabilities. In that case create meaningful child nodes with parent_id. For example, a Rental Services boundary that owns Search, Recommendation, Verification, and Commute Scoring should expose those as children rather than hiding them inside one combined 'Search & Recommendation' component. At least one such rich boundary should be decomposed when the brief clearly contains multiple capabilities. "
                "Do not join independently implementable capabilities with '&' or 'and' merely to avoid hierarchy. Keep them under one coherent parent when they share a system boundary, and express the capabilities as child nodes. "
                "When the Goal names a concrete domain capability, prefer a domain-specific component name and responsibility (for example Rental Search & Recommendation Service) over a generic name such as Backend. Agent orchestration should coordinate domain capabilities rather than absorb their business responsibility. "
                "Do not invent auth, queues, microservices, caches, observability stacks, or other infrastructure unless the Goal justifies them. Prefer 4-6 components for a multi-capability product, but 3 is valid for a genuinely simple product. "
                "Every component must have one crisp responsibility, every component should participate in at least one meaningful relationship, and the graph should make the main end-to-end request/data flow understandable. "
                "Create tasks that cover the critical implementation boundaries instead of assigning every task to one catch-all component. Link each task to the most specific meaningful component or child id. "
                "Keep summary, responsibilities, relationship descriptions, task descriptions, and acceptance criteria concise. "
                "Use stable short lowercase component ids across the entire hierarchy. Relationships and task.related_component may reference any valid node id. Keep the main top-level request/data flow understandable; do not create a relationship for every parent-child pair just to restate hierarchy. "
                "Use at most 3 plain-string decisions, assumptions, and risks each. Do not invent requirements beyond the brief."
                + "\n\nCONFIRMED PROJECT:\n"
                + context.project.model_dump_json()
                + "\n\nBOOTSTRAP EVENT:\n"
                + event.model_dump_json()
            )
        else:
            prompt = (
                system_prompt
                + "\n\nPROJECT CONTEXT (bounded JSON):\n"
                + context.model_dump_json()
                + "\n\nOBSERVED EVENT:\n"
                + event.model_dump_json()
            )

        candidate_chain = (
            self.routine_model_chain
            if is_routine_update
            else self.bootstrap_model_chain
            if is_bootstrap
            else self.model_chain
        )
        per_model_timeout = (
            self.routine_model_timeout_seconds
            if is_routine_update
            else self.architecture_model_timeout_seconds
        )
        total_timeout = (
            max(per_model_timeout, self.architecture_total_timeout_seconds)
            if not is_routine_update
            else per_model_timeout * max(1, len(candidate_chain))
        )
        started = time.perf_counter()
        unavailable: list[str] = []
        timed_out: list[str] = []
        last_unavailable: Exception | None = None

        for candidate in candidate_chain:
            remaining = total_timeout - (time.perf_counter() - started)
            if remaining <= 0:
                break
            self.last_model_id = candidate
            try:
                if is_bootstrap:
                    wire = await asyncio.wait_for(
                        self._invoke_bootstrap(candidate, prompt),
                        timeout=min(per_model_timeout, remaining),
                    )
                    return self._bootstrap_to_domain_decision(wire)
                wire = await asyncio.wait_for(
                    self._invoke(candidate, prompt),
                    timeout=min(per_model_timeout, remaining),
                )
                return self._to_domain_decision(wire, event=event, context=context)
            except TimeoutError as exc:
                timed_out.append(candidate)
                last_unavailable = exc
                continue
            except Exception as exc:
                if not self._is_temporary_unavailable(exc):
                    raise
                unavailable.append(candidate)
                last_unavailable = exc

        details: list[str] = []
        if timed_out:
            details.append("timed out: " + ", ".join(timed_out))
        if unavailable:
            details.append("503 unavailable: " + ", ".join(unavailable))
        models = ", ".join(candidate_chain)
        reason = "; ".join(details) or "overall reasoning deadline reached"
        raise RuntimeError(
            f"Gemini models {models} could not complete within the bounded reasoning window ({reason}). "
            "No project state was changed; retry the event."
        ) from last_unavailable
