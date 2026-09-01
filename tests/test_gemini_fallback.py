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
    GeminiPlannerRootWire,
    GeminiScopeDeltaWire,
    GeminiSystemMapWire,
    InitialArchitecturePlannerSnapshot,
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
    provider.architecture_phase_timeout_seconds = 1.0
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


def test_bootstrap_phase_uses_fast_rescue_chain():
    provider = _provider_with_chain()
    attempts: list[str] = []

    async def fake_system_map(self, model_id: str, prompt: str):
        attempts.append(model_id)
        if model_id == "gemini-3.7-flash":
            raise RuntimeError("503 UNAVAILABLE: model currently experiencing high demand")
        return GeminiSystemMapWire(
            summary="Initial system map.",
            roots=[GeminiPlannerRootWire(id="product", name="Product", type="system", responsibility="Own the product boundary")],
        )

    provider._invoke_system_map = MethodType(fake_system_map, provider)
    wire = asyncio.run(
        provider._run_planner_phase(
            "_invoke_system_map",
            "system map prompt",
            global_deadline=__import__("time").perf_counter() + 2,
        )
    )

    assert attempts == ["gemini-3.7-flash", "gemini-3.5-flash-lite"]
    assert provider.last_model_id == "gemini-3.5-flash-lite"
    assert [root.id for root in wire.roots] == ["product"]


def test_bootstrap_prompt_requests_outside_in_decomposition_without_hardcoded_taxonomy():
    provider = _provider_with_chain()
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
    system_prompt = provider._system_map_prompt(event=event, context=context)
    snapshot = InitialArchitecturePlannerSnapshot(
        roots=("experience",),
        components=(GeminiComponentWire(id="experience", name="Experience", type="system", responsibility="Own user interaction"),),
    )
    scope_prompt = provider._scope_prompt(
        event=event,
        context=context,
        snapshot=snapshot,
        scope_id="experience",
    )

    assert "SYSTEM_MAP phase" in system_prompt
    assert "ONLY root system boundaries" in system_prompt
    assert "Normally use 3-6 truthful major boundaries" in system_prompt
    assert "allow 1-2 for a genuinely simple system" in system_prompt
    assert "do not hardcode a category taxonomy" in system_prompt.lower()
    assert "EXPAND_SCOPE phase" in scope_prompt
    assert "only NEW descendants" in scope_prompt
    assert "never regenerate or edit accepted nodes" in scope_prompt
    assert "files, classes, functions, methods" in scope_prompt
