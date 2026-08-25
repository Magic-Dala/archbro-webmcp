from __future__ import annotations

from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    ProjectContext,
    ProjectEvent,
    ProjectEventType,
    Relationship,
    TaskProposal,
    TaskOwner,
    TaskSource,
)
from archbro.backend.llm.provider import GoalConversationMessage, GoalDraft, ModelProvider


class FakeModelProvider(ModelProvider):
    name = "fake"
    model_id = "deterministic-v0"

    async def draft_goal(
        self,
        *,
        messages: list[GoalConversationMessage],
        current_goal: str = "",
    ) -> GoalDraft:
        all_user_messages = [message.content.strip() for message in messages if message.role == "user" and message.content.strip()]
        baseline = current_goal.strip()
        user_text = all_user_messages[-1] if baseline and all_user_messages else "\n".join(all_user_messages)
        combined = "\n\n".join(part for part in (baseline, user_text) if part).strip()
        lowered = combined.lower()
        has_stack = any(term in lowered for term in ("react", "fastapi", "postgres", "firestore", "backend", "frontend"))
        has_outcome = any(term in lowered for term in ("user", "users", "issue", "task", "create", "view", "update", "build"))
        ready = len(combined) >= 80 and has_outcome and (has_stack or "agent choose" in lowered or "choose the stack" in lowered)

        if not combined:
            return GoalDraft(
                assistant_message="What are you trying to build, and what should a user be able to do in the first usable version?",
                missing_information=["product outcome", "first user workflow"],
            )

        if not user_text and baseline:
            return GoalDraft(
                suggested_project_name="Issue Tracker" if "issue" in lowered else "New Project",
                goal=baseline,
                assistant_message="Your written Goal is ready to refine with Ask, or you can use it directly to generate the first architecture.",
                ready=True,
                missing_information=[],
            )

        if not ready:
            return GoalDraft(
                suggested_project_name="Issue Tracker" if "issue" in lowered else "New Project",
                goal=combined,
                assistant_message="I preserved the current Goal and merged in your Ask. What should the first usable milestone include, and are there any required technologies or constraints?",
                ready=False,
                missing_information=["first usable milestone", "technical constraints"],
            )

        return GoalDraft(
            suggested_project_name="Issue Tracker" if "issue" in lowered else "New Project",
            goal=combined,
            assistant_message="I preserved the existing Goal and merged in this Ask. The draft is specific enough to generate the first architecture, and you can still refine it further.",
            ready=True,
            missing_information=[],
        )

    async def generate(self, *, event: ProjectEvent, context: ProjectContext, system_prompt: str) -> AgentDecision:
        text = str(event.payload.get("message") or event.payload.get("note") or "")
        bootstrap = (
            event.type == ProjectEventType.USER_MESSAGE
            and context.architecture.version == 0
            and event.payload.get("intent") == "INITIAL_ARCHITECTURE"
        )

        if bootstrap:
            components = [
                Component(id="frontend", name="React frontend", type="frontend", responsibility="Project collaboration UI"),
                Component(id="backend", name="FastAPI backend", type="backend", responsibility="REST API and deterministic execution"),
                Component(id="database", name="PostgreSQL", type="database", responsibility="Persist project state"),
            ]
            architecture_payload = {
                "version": 1,
                "summary": "React frontend calls a FastAPI backend backed by PostgreSQL.",
                "components": [c.model_dump(mode="json") for c in components],
                "relationships": [
                    Relationship(source="frontend", target="backend", relationship_type="REST", description="Frontend invokes backend API").model_dump(mode="json"),
                    Relationship(source="backend", target="database", relationship_type="PERSISTENCE", description="Backend persists project state").model_dump(mode="json"),
                ],
                "decisions": ["Use React", "Use FastAPI", "Use PostgreSQL"],
                "assumptions": [],
                "risks": [],
            }
            return AgentDecision(
                summary="Created the initial architecture and actionable implementation tasks from the stored project Goal/Brief.",
                actions=[
                    AgentAction(type=AgentActionType.ADD_PROJECT_NOTE, payload={"note": "INITIAL_ARCHITECTURE:" + __import__("json").dumps(architecture_payload)}),
                    AgentAction(type=AgentActionType.CREATE_TASK, payload={"task": TaskProposal(title="Build FastAPI backend skeleton", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="backend", acceptance_criteria=["FastAPI app starts", "Project endpoints respond"]).model_dump(mode="json")}),
                    AgentAction(type=AgentActionType.CREATE_TASK, payload={"task": TaskProposal(title="Build React frontend shell", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="frontend", acceptance_criteria=["Frontend can render project state"]).model_dump(mode="json")}),
                    AgentAction(type=AgentActionType.CREATE_TASK, payload={"task": TaskProposal(title="Prepare PostgreSQL persistence", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="database", acceptance_criteria=["Backend persistence contract can target PostgreSQL"]).model_dump(mode="json")}),
                ],
            )

        if event.type == ProjectEventType.TASK_UPDATED:
            task_id = event.payload.get("task_id")
            status = event.payload.get("status", "DONE")
            return AgentDecision(
                summary="Updated the reported task while preserving the current architecture.",
                actions=[AgentAction(type=AgentActionType.UPDATE_TASK, payload={"task_id": task_id, "changes": {"status": status}})],
            )

        if event.type == ProjectEventType.USER_MESSAGE and "firestore" in text.lower():
            proposal = ArchitectureChangeProposal(
                project_id=context.project.id,
                reason="The human explicitly changed the persistence requirement from PostgreSQL to Firestore.",
                evidence=[text],
                observed_change="Persistence technology changed from PostgreSQL to Firestore.",
                affected_components=["database", "backend"],
                proposed_changes=[{"operation": "replace_component", "component_id": "database", "new_name": "Firestore", "new_type": "database", "new_responsibility": "Persist project state"}],
                impact="Backend persistence adapter and database-related tasks must be re-evaluated.",
                recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
            )
            return AgentDecision(
                summary="Detected an evidence-backed architecture-impacting requirement change; human approval is required.",
                actions=[AgentAction(type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE, payload={"proposal": proposal.model_dump(mode="json")})],
                architecture_review_required=True,
            )

        return AgentDecision(summary="No justified project-state change was identified.", actions=[AgentAction(type=AgentActionType.NO_ACTION)])
