from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from archbro.backend.agent.evaluation import DriftPolicy
from archbro.backend.agent.prompts import SYSTEM_PROMPT
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentRunResult,
    GitHubChangePayload,
    ObservationClaimState,
    ProjectEvent,
    ProjectEventSource,
    ProjectEventType,
    TaskStatus,
)
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.llm.provider import ModelProvider
from archbro.backend.core.observation import ObservationInProgressError
from archbro.backend.core.repository import ProjectRepositoryPort

logger = logging.getLogger("archbro")


class AgentOrchestrator:
    def __init__(self, repository: ProjectRepositoryPort, provider: ModelProvider) -> None:
        self.repository = repository
        self.provider = provider
        self.executor = ActionExecutor(repository)

    async def observe_event(self, event: ProjectEvent) -> AgentRunResult:
        run_id = f"run_{uuid4().hex}"
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc)

        project = self.repository.get_project(event.project_id)
        context = self.repository.load_context(event.project_id)

        # Canonicalize external provider input before durable observation registration so
        # replayed deliveries compare against exactly the same persisted payload.
        try:
            if event.type == ProjectEventType.GITHUB_CHANGE and event.source == ProjectEventSource.GITHUB:
                if not event.source_event_id:
                    raise ValueError("GITHUB_CHANGE events require source_event_id")
                normalized = GitHubChangePayload.model_validate(event.payload)
                event = event.model_copy(
                    update={"payload": normalized.model_dump(mode="json", exclude_none=True)}
                )

            if context.architecture.version == 0:
                if not project.goal.strip():
                    raise ValueError("project goal is required before generating the initial architecture")
                if event.type != ProjectEventType.USER_MESSAGE or event.payload.get("intent") != "INITIAL_ARCHITECTURE":
                    raise ValueError(
                        "initial architecture has not been generated; use the project Goal/Brief and Generate initial architecture first"
                    )

                brief = project.goal.strip()
                if project.description.strip():
                    brief += "\n\nAdditional project context:\n" + project.description.strip()
                event = event.model_copy(
                    update={"payload": {"intent": "INITIAL_ARCHITECTURE", "message": brief}}
                )
            elif event.payload.get("intent") == "INITIAL_ARCHITECTURE":
                raise ValueError("initial architecture already exists")
        except ValueError as exc:
            used_model = getattr(self.provider, "last_model_id", self.provider.model_id)
            result = AgentRunResult(
                project_id=event.project_id,
                event_id=event.id,
                agent_run_id=run_id,
                summary="Agent run failed before observation registration.",
                actions=[],
                architecture_review_required=False,
                proposal_ids=[],
                evaluation=None,
                provider=self.provider.name,
                model=used_model,
                result="ERROR",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            return result

        claim = self.repository.claim_observation(event, run_id=run_id)
        if claim.state == ObservationClaimState.REPLAY:
            if claim.existing_result is None:
                raise RuntimeError("completed observation is missing its durable AgentRun")
            return claim.existing_result.model_copy(update={"replayed": True})
        if claim.state == ObservationClaimState.IN_PROGRESS:
            raise ObservationInProgressError(
                f"observation {claim.event.id} is already being evaluated"
            )

        event = claim.event
        run_id = claim.run_id
        context = self.repository.load_context(event.project_id)

        try:
            # A human clicking Start/Done is authoritative project state, not a model
            # suggestion. Apply the explicit transition deterministically, persist the
            # observation, and let later agent/project signals evaluate implications.
            if event.type == ProjectEventType.TASK_UPDATED:
                if event.source not in {ProjectEventSource.HUMAN, ProjectEventSource.FRONTEND}:
                    raise ValueError("authoritative TASK_UPDATED transitions require HUMAN or FRONTEND provenance")
                task_id = str(event.payload.get("task_id", "")).strip()
                if not task_id or not any(task.id == task_id for task in context.tasks):
                    raise ValueError("TASK_UPDATED requires a task_id from this project")
                status = TaskStatus(str(event.payload.get("status", "")))
                action = AgentAction(
                    type=AgentActionType.UPDATE_TASK,
                    payload={"task_id": task_id, "changes": {"status": status.value}},
                )
                plan = self.executor.build_plan(
                    event.project_id,
                    [action],
                    evidence_event_id=event.id,
                )
                result = AgentRunResult(
                    project_id=event.project_id,
                    event_id=event.id,
                    agent_run_id=run_id,
                    summary=f"Human task state accepted: {status.value}.",
                    actions=[action],
                    architecture_review_required=False,
                    proposal_ids=[],
                    evaluation=None,
                    provider="deterministic",
                    model="human-task-transition",
                    result="SUCCESS",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
                self.repository.commit_observation_result(
                    event=event,
                    run_id=run_id,
                    plan=plan,
                    result=result,
                )
                latency_ms = round((time.perf_counter() - started) * 1000)
                logger.info(
                    "agent_run project_id=%s event_id=%s agent_run_id=%s provider=%s model=%s latency_ms=%s action_count=%s result=%s",
                    event.project_id,
                    event.id,
                    run_id,
                    result.provider,
                    result.model,
                    latency_ms,
                    len(result.actions),
                    result.result,
                )
                return result

            decision = await self.provider.generate(event=event, context=context, system_prompt=SYSTEM_PROMPT)

            # M4 safety boundary: provider output must be fully validated before the
            # observed event or any model-derived product mutation is persisted.
            DriftPolicy.validate(context, decision)
            if event.source not in {ProjectEventSource.HUMAN, ProjectEventSource.FRONTEND} and any(
                action.type == AgentActionType.UPDATE_PROJECT_STATUS for action in decision.actions
            ):
                raise ValueError("external observations cannot directly change project status")
            self.executor.validate_all(event.project_id, decision.actions)
            plan = self.executor.build_plan(
                event.project_id,
                decision.actions,
                evidence_event_id=event.id,
            )
            used_model = getattr(self.provider, "last_model_id", self.provider.model_id)
            result = AgentRunResult(
                project_id=event.project_id,
                event_id=event.id,
                agent_run_id=run_id,
                summary=decision.summary,
                actions=decision.actions,
                architecture_review_required=decision.architecture_review_required,
                proposal_ids=plan.proposal_ids,
                evaluation=decision.evaluation,
                provider=self.provider.name,
                model=used_model,
                result="SUCCESS",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            self.repository.commit_observation_result(
                event=event,
                run_id=run_id,
                plan=plan,
                result=result,
            )
        except Exception as exc:
            used_model = getattr(self.provider, "last_model_id", self.provider.model_id)
            result = AgentRunResult(
                project_id=event.project_id,
                event_id=event.id,
                agent_run_id=run_id,
                summary="Agent run failed before state mutation.",
                actions=[],
                architecture_review_required=False,
                proposal_ids=[],
                evaluation=None,
                provider=self.provider.name,
                model=used_model,
                result="ERROR",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            self.repository.fail_observation(event=event, run_id=run_id, result=result)
        latency_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "agent_run project_id=%s event_id=%s agent_run_id=%s provider=%s model=%s latency_ms=%s action_count=%s result=%s",
            event.project_id,
            event.id,
            run_id,
            self.provider.name,
            result.model,
            latency_ms,
            len(result.actions),
            result.result,
        )
        return result
