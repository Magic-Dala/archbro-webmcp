import asyncio
from types import MethodType

import pytest

from archbro.backend.core.contracts import AgentAction, AgentActionType, Architecture, Project, ProjectContext, ProjectEvent, ProjectEventType
from archbro.backend.core.evaluation import DriftClassification, DriftEvaluation, DriftRecommendedAction
from archbro.backend.llm.gemini import (
    DEFAULT_GEMINI_GOAL_CHAIN,
    DEFAULT_GEMINI_ROUTINE_CHAIN,
    GeminiArchitectureWire,
    GeminiBootstrapWire,
    GeminiComponentWire,
    GeminiDecisionWire,
    GeminiProvider,
)


def _aligned_evaluation() -> DriftEvaluation:
    return DriftEvaluation(
        classification=DriftClassification.ALIGNED,
        summary="No architecture drift in fallback-routing fixture.",
        recommended_action=DriftRecommendedAction.NO_ACTION,
    )

def _provider_with_chain() -> GeminiProvider:
    provider = object.__new__(GeminiProvider)
    provider.model_id = "gemini-3.7-flash"
    provider.last_model_id = provider.model_id
    provider.fallback_model_ids = (
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    )
    provider.routine_model_id = "gemini-3.5-flash-lite"
    provider.routine_fallback_model_ids = (
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
    )
    provider.routine_model_timeout_seconds = 0.5
    provider.architecture_model_timeout_seconds = 0.5
    provider.architecture_total_timeout_seconds = 2.0
    provider.bootstrap_fallback_model_ids = (
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )
    return provider


def _context_and_event(event_type: ProjectEventType = ProjectEventType.MANUAL_NOTE):
    project = Project(name="fallback test", goal="verify provider fallback")
    context = ProjectContext(project=project, architecture=Architecture(version=1), tasks=[], pending_proposals=[])
    if event_type == ProjectEventType.TASK_UPDATED:
        payload = {"task_id": "task_test", "status": "DONE", "message": "Task completed."}
    elif event_type == ProjectEventType.USER_MESSAGE:
        payload = {"message": "We may need to change the architecture."}
    else:
        payload = {"note": "No state change."}
    event = ProjectEvent(project_id=project.id, type=event_type, payload=payload)
    return context, event


def test_503_falls_through_full_real_gemini_chain():
    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_invoke(self, model_id: str, prompt: str):
        attempts.append(model_id)
        if model_id != "gemini-3.5-flash-lite":
            raise RuntimeError("503 UNAVAILABLE: model currently experiencing high demand")
        return GeminiDecisionWire(
            summary="No change.",
            evaluation=_aligned_evaluation(),
            actions=[AgentAction(type=AgentActionType.NO_ACTION)],
        )

    provider._invoke = MethodType(fake_invoke, provider)
    context, event = _context_and_event()
    decision = asyncio.run(provider.generate(event=event, context=context, system_prompt="test"))

    assert attempts == [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert provider.last_model_id == "gemini-3.5-flash-lite"
    assert decision.actions[0].type == AgentActionType.NO_ACTION


def test_429_does_not_fallback():
    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_invoke(self, model_id: str, prompt: str):
        attempts.append(model_id)
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    provider._invoke = MethodType(fake_invoke, provider)
    context, event = _context_and_event()

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(provider.generate(event=event, context=context, system_prompt="test"))

    assert attempts == ["gemini-3.7-flash"]
    assert provider.last_model_id == "gemini-3.7-flash"


def test_goal_and_routine_chains_use_high_quota_flash_lite_models_first():
    assert DEFAULT_GEMINI_GOAL_CHAIN[:2] == (
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    )
    assert DEFAULT_GEMINI_ROUTINE_CHAIN[:2] == (
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    )


def test_strands_agent_instances_are_not_shared_between_invocations():
    provider = object.__new__(GeminiProvider)

    def fake_build(self, model_id: str):
        return object()

    provider._build_agent = MethodType(fake_build, provider)
    first = provider._agent_for("gemini-3.5-flash-lite")
    second = provider._agent_for("gemini-3.5-flash-lite")

    assert first is not second


def test_task_updated_uses_routine_chain_before_architecture_models():
    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_invoke(self, model_id: str, prompt: str):
        attempts.append(model_id)
        if model_id == "gemini-3.5-flash-lite":
            raise RuntimeError("503 UNAVAILABLE: model currently experiencing high demand")
        if model_id == "gemini-3.1-flash-lite":
            return GeminiDecisionWire(
                summary="Routine task update handled.",
                evaluation=_aligned_evaluation(),
                actions=[AgentAction(type=AgentActionType.NO_ACTION)],
            )
        raise AssertionError(f"unexpected model reached: {model_id}")

    provider._invoke = MethodType(fake_invoke, provider)
    context, event = _context_and_event(ProjectEventType.TASK_UPDATED)
    decision = asyncio.run(provider.generate(event=event, context=context, system_prompt="test"))

    assert attempts == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    assert provider.last_model_id == "gemini-3.1-flash-lite"
    assert decision.actions[0].type == AgentActionType.NO_ACTION


def test_user_message_stays_on_architecture_chain():
    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_invoke(self, model_id: str, prompt: str):
        attempts.append(model_id)
        return GeminiDecisionWire(
            summary="Architecture-sensitive message handled.",
            evaluation=_aligned_evaluation(),
            actions=[AgentAction(type=AgentActionType.NO_ACTION)],
        )

    provider._invoke = MethodType(fake_invoke, provider)
    context, event = _context_and_event(ProjectEventType.USER_MESSAGE)
    asyncio.run(provider.generate(event=event, context=context, system_prompt="test"))

    assert attempts == ["gemini-3.7-flash"]
    assert provider.last_model_id == "gemini-3.7-flash"


def test_bootstrap_uses_slim_wire_and_fast_rescue_chain():
    from archbro.backend.core.contracts import Relationship, TaskProposal

    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_bootstrap(self, model_id: str, prompt: str):
        attempts.append(model_id)
        if model_id == "gemini-3.7-flash":
            raise RuntimeError("503 UNAVAILABLE: model currently experiencing high demand")
        return GeminiBootstrapWire(
            summary="Initial V0 architecture created.",
            architecture=GeminiArchitectureWire(
                version=1,
                summary="Small V0",
                components=[
                    GeminiComponentWire(id="frontend", name="Frontend", type="web", responsibility="UI"),
                    GeminiComponentWire(id="backend", name="Backend", type="service", responsibility="API"),
                    GeminiComponentWire(id="database", name="Database", type="database", responsibility="Persistence"),
                ],
                relationships=[
                    Relationship(source="frontend", target="backend", relationship_type="calls"),
                    Relationship(source="backend", target="database", relationship_type="reads_writes"),
                ],
            ),
            tasks=[TaskProposal(title="Build backend", related_component="backend")],
        )

    provider._invoke_bootstrap = MethodType(fake_bootstrap, provider)
    project = Project(name="bootstrap", goal="Build a small web product")
    context = ProjectContext(project=project, architecture=Architecture(), tasks=[], pending_proposals=[])
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE", "message": project.goal},
    )
    decision = asyncio.run(provider.generate(event=event, context=context, system_prompt="unused for slim bootstrap"))

    assert attempts == ["gemini-3.7-flash", "gemini-3.5-flash-lite"]
    assert provider.last_model_id == "gemini-3.5-flash-lite"
    assert decision.architecture_review_required is False
    assert [action.type for action in decision.actions] == [AgentActionType.ADD_PROJECT_NOTE, AgentActionType.CREATE_TASK]


def test_bootstrap_prompt_requests_meaningful_decomposition_without_forcing_component_count():
    from archbro.backend.core.contracts import Relationship, TaskProposal

    provider = _provider_with_chain()
    captured_prompt = ""

    async def fake_bootstrap(self, model_id: str, prompt: str):
        nonlocal captured_prompt
        captured_prompt = prompt
        return GeminiBootstrapWire(
            summary="Initial architecture.",
            architecture=GeminiArchitectureWire(
                version=1,
                summary="V0",
                components=[
                    GeminiComponentWire(id="frontend", name="Frontend", type="web", responsibility="User experience"),
                    GeminiComponentWire(id="agent", name="Agent", type="agent", responsibility="Agent orchestration"),
                    GeminiComponentWire(id="data", name="Data", type="database", responsibility="Persistence"),
                ],
                relationships=[
                    Relationship(source="frontend", target="agent", relationship_type="calls"),
                    Relationship(source="agent", target="data", relationship_type="reads_writes"),
                ],
            ),
            tasks=[TaskProposal(title="Build agent", related_component="agent")],
        )

    provider._invoke_bootstrap = MethodType(fake_bootstrap, provider)
    project = Project(
        name="rental",
        goal="Build an agentic rental site with user UI, recommendations, search, and managed data on Google Cloud.",
    )
    context = ProjectContext(project=project, architecture=Architecture(), tasks=[], pending_proposals=[])
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE", "message": project.goal},
    )

    asyncio.run(provider.generate(event=event, context=context, system_prompt="unused"))

    assert "distinct responsibility" in captured_prompt
    assert "parent_id" in captured_prompt
    assert "Do not output nested children arrays" in captured_prompt
    assert "do not leave the entire architecture flat" in captured_prompt
    assert "independently implementable capabilities" in captured_prompt
    assert "external search/recommendation/data integrations" in captured_prompt
    assert "prefer a domain-specific component name" in captured_prompt
    assert "Agent orchestration should coordinate domain capabilities" in captured_prompt
    assert "Prefer 4-6 components for a multi-capability product" in captured_prompt
    assert "3 is valid for a genuinely simple product" in captured_prompt
