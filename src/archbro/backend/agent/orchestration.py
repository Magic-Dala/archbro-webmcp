from __future__ import annotations

import logging
import time
from uuid import uuid4

from archbro.backend.agent.prompts import SYSTEM_PROMPT
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentRunResult,
    ProjectEvent,
    ProjectEventType,
    TaskStatus,
)
from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.llm.provider import ModelProvider
from archbro.backend.core.repository import ProjectRepositoryPort

logger = logging.getLogger("archbro")


class AgentOrchestrator:
    def __init__(self, repository: ProjectRepositoryPort, provider: ModelProvider) -> None:
        self.repository = repository
        self.provider = provider
        self.executor = ActionExecutor(repository)

    async def observe_event(self, event: ProjectEvent) -> AgentRunResult:
        project = self.repository.get_project(event.project_id)
        context = self.repository.load_context(event.project_id)
        before = self.repository.snapshot(event.project_id)
        run_id = f"run_{uuid4().hex}"
        started = time.perf_counter()

        try:
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

            # A human clicking Start/Done is authoritative project state, not a model
            # suggestion. Apply the explicit transition deterministically, persist the
            # observation, and let later agent/project signals evaluate implications.
            if event.type == ProjectEventType.TASK_UPDATED:
                task_id = str(event.payload.get("task_id", "")).strip()
                if not task_id or not any(task.id == task_id for task in context.tasks):
                    raise ValueError("TASK_UPDATED requires a task_id from this project")
                status = TaskStatus(str(event.payload.get("status", "")))
                action = AgentAction(
                    type=AgentActionType.UPDATE_TASK,
                    payload={"task_id": task_id, "changes": {"status": status.value}},
                )
                self.repository.save_event(event)
                self.executor.validate_all(event.project_id, [action])
                self.executor.apply(event.project_id, [action])
                result = AgentRunResult(
                    project_id=event.project_id,
                    event_id=event.id,
                    agent_run_id=run_id,
                    summary=f"Human task state accepted: {status.value}.",
                    actions=[action],
                    architecture_review_required=False,
                    proposal_ids=[],
                    provider="deterministic",
                    model="human-task-transition",
                    result="SUCCESS",
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

            self.repository.save_event(event)
            decision = await self.provider.generate(event=event, context=context, system_prompt=SYSTEM_PROMPT)
            self.executor.validate_all(event.project_id, decision.actions)
            proposal_ids = self.executor.apply(event.project_id, decision.actions)
            used_model = getattr(self.provider, "last_model_id", self.provider.model_id)
            result = AgentRunResult(
                project_id=event.project_id,
                event_id=event.id,
                agent_run_id=run_id,
                summary=decision.summary,
                actions=decision.actions,
                architecture_review_required=decision.architecture_review_required,
                proposal_ids=proposal_ids,
                provider=self.provider.name,
                model=used_model,
                result="SUCCESS",
            )
        except Exception as exc:
            after = self.repository.snapshot(event.project_id)
            if after != before:
                raise RuntimeError("provider/validation failure mutated project state") from exc
            used_model = getattr(self.provider, "last_model_id", self.provider.model_id)
            result = AgentRunResult(
                project_id=event.project_id,
                event_id=event.id,
                agent_run_id=run_id,
                summary="Agent run failed before state mutation.",
                actions=[],
                architecture_review_required=False,
                proposal_ids=[],
                provider=self.provider.name,
                model=used_model,
                result="ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
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
