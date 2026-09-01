import asyncio
import json
import time
from types import MethodType

import pytest
from pydantic import ValidationError

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import (
    Architecture,
    Component,
    Project,
    ProjectContext,
    ProjectEvent,
    ProjectEventType,
    Relationship,
    TaskProposal,
)
from archbro.backend.llm.gemini import (
    ArchitectureNeedsFactError,
    GeminiPlannerRootWire,
    GeminiProvider,
    GeminiReconcileWire,
    GeminiScopeDeltaWire,
    GeminiSystemMapWire,
    GeminiComponentWire,
    InitialArchitecturePlannerSnapshot,
)
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database


def _provider() -> GeminiProvider:
    provider = object.__new__(GeminiProvider)
    provider.model_id = "gemini-primary"
    provider.last_model_id = provider.model_id
    provider.bootstrap_fallback_model_ids = ("gemini-rescue",)
    provider.fallback_model_ids = ()
    provider.routine_model_id = "gemini-routine"
    provider.routine_fallback_model_ids = ()
    provider.routine_model_timeout_seconds = 0.5
    provider.architecture_model_timeout_seconds = 0.5
    provider.architecture_phase_timeout_seconds = 1.5
    provider.architecture_total_timeout_seconds = 8.0
    return provider


def _bootstrap_context(root_count: int = 4) -> tuple[ProjectContext, ProjectEvent]:
    project = Project(
        name="outside-in-fixture",
        goal=(
            "Build a collaborative product with a user workspace, domain APIs, "
            "agent coordination, durable project state, and external integrations."
        ),
    )
    context = ProjectContext(
        project=project,
        architecture=Architecture(),
        tasks=[],
        pending_proposals=[],
    )
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE", "message": project.goal},
    )
    return context, event


def _roots() -> list[GeminiPlannerRootWire]:
    return [
        GeminiPlannerRootWire(id="experience", name="Experience", type="system", responsibility="Own user interaction"),
        GeminiPlannerRootWire(id="domain", name="Domain", type="system", responsibility="Own product behavior"),
        GeminiPlannerRootWire(id="coordination", name="Coordination", type="system", responsibility="Own agent coordination"),
        GeminiPlannerRootWire(id="state", name="State", type="system", responsibility="Own durable state and external data"),
    ]


def _wire_provider(provider: GeminiProvider, calls: list[str], *, fail_scope: str | None = None, fail_reconcile: bool = False) -> None:
    async def system_map(self, model_id: str, prompt: str):
        calls.append("SYSTEM_MAP")
        return GeminiSystemMapWire(summary="Four truthful boundaries", roots=_roots())

    async def scope_delta(self, model_id: str, prompt: str):
        scope_id = prompt.split("scope_id=", 1)[1].split(".", 1)[0]
        calls.append(f"EXPAND_SCOPE:{scope_id}")
        if scope_id == fail_scope:
            raise RuntimeError("scope failed permanently")
        child_id = f"{scope_id}_capability"
        return GeminiScopeDeltaWire(
            scope_id=scope_id,
            components=[
                GeminiComponentWire(
                    id=child_id,
                    name=f"{scope_id.title()} Capability",
                    type="service",
                    responsibility=f"Implement the {scope_id} capability",
                    parent_id=scope_id,
                )
            ],
        )

    async def reconcile(self, model_id: str, prompt: str):
        calls.append("RECONCILE")
        if fail_reconcile:
            raise RuntimeError("reconcile failed permanently")
        return GeminiReconcileWire(
            summary="Outside-in architecture ready",
            relationships=[
                Relationship(source="experience_capability", target="domain_capability", relationship_type="HTTPS"),
                Relationship(source="domain_capability", target="state_capability", relationship_type="STATE"),
            ],
            tasks=[TaskProposal(title="Build domain capability", related_component="domain_capability")],
        )

    provider._invoke_system_map = MethodType(system_map, provider)
    provider._invoke_scope_delta = MethodType(scope_delta, provider)
    provider._invoke_reconcile = MethodType(reconcile, provider)


def _architecture_from_decision(decision) -> Architecture:
    note = next(action.payload["note"] for action in decision.actions if action.payload.get("note", "").startswith("INITIAL_ARCHITECTURE:"))
    return Architecture.model_validate_json(note.removeprefix("INITIAL_ARCHITECTURE:"))


def test_rich_bootstrap_runs_ordered_outside_in_passes_and_emits_once():
    provider = _provider()
    calls: list[str] = []
    _wire_provider(provider, calls)
    context, event = _bootstrap_context()

    decision = asyncio.run(provider.generate(event=event, context=context, system_prompt="unused"))
    architecture = _architecture_from_decision(decision)

    assert calls == [
        "SYSTEM_MAP",
        "EXPAND_SCOPE:experience",
        "EXPAND_SCOPE:domain",
        "EXPAND_SCOPE:coordination",
        "EXPAND_SCOPE:state",
        "RECONCILE",
    ]
    assert len(calls) == 6
    assert [component.id for component in architecture.components] == ["experience", "domain", "coordination", "state"]
    assert architecture.find_component("domain_capability") is not None
    assert architecture.parent_component_id_for("domain_capability") == "domain"
    assert architecture.root_component_id_for("domain_capability") == "domain"


def test_default_planner_time_budget_is_12_18_36(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_ARCHITECTURE_MODEL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("GEMINI_ARCHITECTURE_PHASE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("GEMINI_ARCHITECTURE_TOTAL_TIMEOUT_SECONDS", raising=False)
    provider = GeminiProvider(model_id="gemini-test")
    assert provider.architecture_model_timeout_seconds == 12
    assert provider.architecture_phase_timeout_seconds == 18
    assert provider.architecture_total_timeout_seconds == 36


def test_system_map_accepts_simple_roots_but_rejects_more_than_six():
    one = GeminiSystemMapWire(roots=[GeminiPlannerRootWire(id="only", name="Only", type="system", responsibility="Whole simple product")])
    two = GeminiSystemMapWire(roots=[
        GeminiPlannerRootWire(id="a", name="A", type="system", responsibility="A"),
        GeminiPlannerRootWire(id="b", name="B", type="system", responsibility="B"),
    ])
    assert len(one.roots) == 1
    assert len(two.roots) == 2
    with pytest.raises(ValidationError):
        GeminiSystemMapWire(roots=[
            GeminiPlannerRootWire(id=str(index), name=str(index), type="system", responsibility=str(index))
            for index in range(7)
        ])


def test_scope_delta_rejects_existing_id_redefinition_and_cross_scope_parentage():
    snapshot = InitialArchitecturePlannerSnapshot(
        roots=("a", "b"),
        components=(
            GeminiComponentWire(id="a", name="A", type="system", responsibility="A"),
            GeminiComponentWire(id="b", name="B", type="system", responsibility="B"),
        ),
    )
    with pytest.raises(ValueError, match="redefine existing"):
        GeminiProvider._apply_scope_delta(
            snapshot,
            GeminiScopeDeltaWire(
                scope_id="a",
                components=[GeminiComponentWire(id="b", name="Other B", type="service", responsibility="bad", parent_id="a")],
            ),
            expected_scope_id="a",
        )
    with pytest.raises(ValueError, match="only extend"):
        GeminiProvider._apply_scope_delta(
            snapshot,
            GeminiScopeDeltaWire(
                scope_id="a",
                components=[GeminiComponentWire(id="x", name="X", type="service", responsibility="bad", parent_id="b")],
            ),
            expected_scope_id="a",
        )


    with pytest.raises(ValueError, match="unknown parent_id"):
        GeminiProvider._apply_scope_delta(
            snapshot,
            GeminiScopeDeltaWire(
                scope_id="a",
                components=[GeminiComponentWire(id="orphan", name="Orphan", type="service", responsibility="bad", parent_id="missing")],
            ),
            expected_scope_id="a",
        )


def test_scope_delta_fallback_reuses_identical_pre_phase_prompt():
    provider = _provider()
    prompts: list[tuple[str, str]] = []

    async def scope_delta(self, model_id: str, prompt: str):
        prompts.append((model_id, prompt))
        if model_id == "gemini-primary":
            raise RuntimeError("503 UNAVAILABLE: high demand")
        return GeminiScopeDeltaWire(scope_id="a", components=[])

    provider._invoke_scope_delta = MethodType(scope_delta, provider)
    wire = asyncio.run(
        provider._run_planner_phase(
            "_invoke_scope_delta",
            "same immutable phase prompt",
            global_deadline=time.perf_counter() + 5,
        )
    )
    assert wire.scope_id == "a"
    assert [model for model, _ in prompts] == ["gemini-primary", "gemini-rescue"]
    assert prompts[0][1] == prompts[1][1] == "same immutable phase prompt"


def test_needs_fact_is_typed_and_carries_no_partial_architecture():
    provider = _provider()

    async def system_map(self, model_id: str, prompt: str):
        return GeminiSystemMapWire(status="NEEDS_FACT", missing_facts=["Which external data authority is required?"])

    provider._invoke_system_map = MethodType(system_map, provider)
    context, event = _bootstrap_context()
    with pytest.raises(ArchitectureNeedsFactError, match="external data authority"):
        asyncio.run(provider.generate(event=event, context=context, system_prompt="unused"))


def _repo(dsn: str) -> tuple[PostgresProjectRepository, Project]:
    repo = PostgresProjectRepository(dsn)
    project = Project(name="planner", goal="Build an outside-in architecture with multiple responsibilities.")
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture())
    return repo, project


def _repo_event(project: Project) -> ProjectEvent:
    return ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE", "message": project.goal},
    )


@requires_database
def test_mid_scope_failure_keeps_repository_snapshot_unchanged(dsn):
    repo, project = _repo(dsn)
    before = repo.snapshot(project.id)
    provider = _provider()
    calls: list[str] = []
    _wire_provider(provider, calls, fail_scope="domain")

    result = asyncio.run(AgentOrchestrator(repo, provider).observe_event(_repo_event(project)))

    assert result.result == "ERROR"
    assert "scope failed permanently" in result.error
    assert repo.snapshot(project.id) == before
    assert repo.get_architecture(project.id).version == 0


@requires_database
def test_reconcile_failure_keeps_repository_snapshot_unchanged(dsn):
    repo, project = _repo(dsn)
    before = repo.snapshot(project.id)
    provider = _provider()
    calls: list[str] = []
    _wire_provider(provider, calls, fail_reconcile=True)

    result = asyncio.run(AgentOrchestrator(repo, provider).observe_event(_repo_event(project)))

    assert result.result == "ERROR"
    assert "reconcile failed permanently" in result.error
    assert repo.snapshot(project.id) == before
    assert repo.get_architecture(project.id).version == 0


@requires_database
def test_ready_commits_one_serializable_hierarchical_architecture(dsn):
    repo, project = _repo(dsn)
    provider = _provider()
    calls: list[str] = []
    _wire_provider(provider, calls)

    result = asyncio.run(AgentOrchestrator(repo, provider).observe_event(_repo_event(project)))
    accepted = repo.get_architecture(project.id)
    round_trip = Architecture.model_validate_json(accepted.model_dump_json())

    assert result.result == "SUCCESS"
    assert accepted.version == 1
    assert round_trip == accepted
    assert accepted.child_component_ids_for("domain") == ["domain_capability"]
    assert accepted.parent_component_id_for("domain") is None
    assert accepted.parent_component_id_for("domain_capability") == "domain"
    assert len(repo.list_tasks(project.id)) == 1


def test_hierarchy_helpers_are_deterministic_for_three_levels_and_unknown_ids():
    architecture = Architecture(
        version=1,
        components=[
            Component(
                id="root",
                name="Root",
                type="system",
                responsibility="Root",
                children=[
                    Component(
                        id="child",
                        name="Child",
                        type="service",
                        responsibility="Child",
                        children=[Component(id="leaf", name="Leaf", type="tool", responsibility="Leaf")],
                    )
                ],
            )
        ],
    )
    assert architecture.root_component_id_for("leaf") == "root"
    assert architecture.parent_component_id_for("root") is None
    assert architecture.parent_component_id_for("child") == "root"
    assert architecture.parent_component_id_for("leaf") == "child"
    assert architecture.child_component_ids_for("root") == ["child"]
    assert architecture.child_component_ids_for("leaf") == []
    assert architecture.parent_component_id_for("missing") is None
    assert architecture.child_component_ids_for("missing") == []
