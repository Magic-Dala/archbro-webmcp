from __future__ import annotations

import json
import os


from archbro.platform.runtime.app import build_app
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    AgentDecision,
    ArchitectureChangeProposal,
    ArchitectureNodeKind,
    ArchitectureOption,
    Component,
    ProjectContext,
    ProjectEvent,
    ProjectEventType,
    Relationship,
    TaskOwner,
    TaskProposal,
    TaskSource,
)
from archbro.backend.llm.provider import GoalConversationMessage, GoalDraft, ModelProvider
from archbro.platform.persistence.repository import ProjectRepository


class ManualDemoProvider(ModelProvider):
    name = "manual-demo"
    model_id = "human-simulated-api"

    async def draft_goal(self, *, messages: list[GoalConversationMessage], current_goal: str = "") -> GoalDraft:
        asks = [m.content.strip() for m in messages if m.role == "user" and m.content.strip()]
        merged = "\n\n".join(part for part in [current_goal.strip(), asks[-1] if asks else ""] if part).strip()
        return GoalDraft(
            suggested_project_name="AI Rental Assistant",
            goal=merged,
            assistant_message="I preserved the current Goal and merged in your latest Ask. This is ready for a V0 architecture.",
            ready=bool(merged),
            missing_information=[],
        )

    async def generate(self, *, event: ProjectEvent, context: ProjectContext, system_prompt: str) -> AgentDecision:
        message = str(event.payload.get("message") or "")
        lower = message.lower()
        ui_context = event.payload.get("ui_context") or {}
        is_bootstrap = (
            event.type == ProjectEventType.USER_MESSAGE
            and context.architecture.version == 0
            and event.payload.get("intent") == "INITIAL_ARCHITECTURE"
        )

        if is_bootstrap:
            components = [
                Component(
                    id="experience",
                    name="Rental Experience",
                    type="frontend",
                    kind=ArchitectureNodeKind.UI,
                    responsibility="Search, inspect, compare, and shortlist rental homes.",
                ),
                Component(
                    id="agent_runtime",
                    name="Agent Orchestration",
                    type="agent_orchestration",
                    kind=ArchitectureNodeKind.AGENT,
                    responsibility="Understand user intent and coordinate rental capabilities.",
                ),
                Component(
                    id="rental_domain",
                    name="Rental Domain Services",
                    type="domain_service",
                    kind=ArchitectureNodeKind.SERVICE,
                    responsibility="Own rental discovery and decision-support capabilities.",
                    children=[
                        Component(id="rental_search", name="Rental Search", type="domain_service", kind=ArchitectureNodeKind.SERVICE, responsibility="Find candidate listings from user constraints."),
                        Component(id="recommendation", name="Recommendation", type="domain_service", kind=ArchitectureNodeKind.SERVICE, responsibility="Rank and explain personalized rental matches."),
                        Component(id="listing_verification", name="Listing Verification", type="domain_service", kind=ArchitectureNodeKind.SERVICE, responsibility="Verify listing identity and quality before recommendation."),
                    ],
                ),
                Component(
                    id="data_state",
                    name="Data & State",
                    type="data_boundary",
                    kind=ArchitectureNodeKind.SYSTEM,
                    responsibility="Own persistent user and rental application state.",
                    children=[
                        Component(id="app_data_store", name="Firestore", type="firestore", kind=ArchitectureNodeKind.DATA_STORE, responsibility="Persist profiles, favorites, history, and shortlist state."),
                        Component(id="session_state", name="Agent Session State", type="state", kind=ArchitectureNodeKind.STATE, responsibility="Keep short-lived conversational and workflow state."),
                    ],
                ),
                Component(
                    id="external_services",
                    name="External Services",
                    type="external_boundary",
                    kind=ArchitectureNodeKind.SYSTEM,
                    responsibility="Provide location and third-party rental data.",
                    children=[
                        Component(id="maps_api", name="Google Maps", type="maps_api", kind=ArchitectureNodeKind.EXTERNAL_SERVICE, responsibility="Provide geocoding and commute estimates."),
                        Component(id="rental_provider", name="Rental Data Provider", type="rental_api", kind=ArchitectureNodeKind.EXTERNAL_SERVICE, responsibility="Provide external rental listing inventory."),
                    ],
                ),
            ]
            architecture = {
                "version": 1,
                "summary": "A rental web experience uses an agent orchestrator to coordinate rental-domain services, persistent state, and external location/listing providers.",
                "components": [component.model_dump(mode="json") for component in components],
                "relationships": [
                    Relationship(source="experience", target="agent_runtime", relationship_type="USES", description="User requests enter through the rental experience.").model_dump(mode="json"),
                    Relationship(source="agent_runtime", target="rental_domain", relationship_type="ORCHESTRATES", description="The agent coordinates rental-domain capabilities.").model_dump(mode="json"),
                    Relationship(source="rental_search", target="rental_provider", relationship_type="QUERIES", description="Search retrieves external listings.").model_dump(mode="json"),
                    Relationship(source="listing_verification", target="rental_provider", relationship_type="VERIFIES", description="Verification checks listing identity against provider data.").model_dump(mode="json"),
                    Relationship(source="recommendation", target="maps_api", relationship_type="USES", description="Ranking includes commute/location evidence.").model_dump(mode="json"),
                    Relationship(source="rental_domain", target="app_data_store", relationship_type="READS_WRITES", description="Rental workflows persist application state.").model_dump(mode="json"),
                ],
                "decisions": [
                    "Keep agent orchestration separate from rental-domain responsibilities.",
                    "Use Firestore for V0 persistent application data.",
                    "Keep external rental and maps providers behind explicit boundaries.",
                ],
                "assumptions": ["The rental data provider exposes enough metadata for a V0 listing identity strategy."],
                "risks": ["External rental listing identity may be inconsistent across provider responses."],
            }
            tasks = [
                TaskProposal(title="Build rental web experience", description="Implement search, details, shortlist, and compare entry points.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="experience"),
                TaskProposal(title="Configure Agent Orchestration", description="Wire user intent to rental-domain capabilities.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="agent_runtime"),
                TaskProposal(title="Implement Rental Search", description="Query and normalize candidate rental listings.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="rental_search"),
                TaskProposal(title="Implement Recommendation", description="Rank rentals and explain why each matches the user.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="recommendation"),
                TaskProposal(title="Implement Listing Verification", description="Establish stable listing identity and verification rules.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="listing_verification"),
                TaskProposal(title="Prepare Firestore application data", description="Persist profiles, favorites, history, and shortlist state in Firestore.", owner=TaskOwner.HUMAN, source=TaskSource.ARCHITECTURE, related_component="app_data_store"),
            ]
            return AgentDecision(
                summary="Created a hierarchical V0 rental architecture and human implementation tasks.",
                actions=[AgentAction(type=AgentActionType.ADD_PROJECT_NOTE, payload={"note": "INITIAL_ARCHITECTURE:" + json.dumps(architecture)})]
                + [AgentAction(type=AgentActionType.CREATE_TASK, payload={"task": task.model_dump(mode="json")}) for task in tasks],
            )

        if event.type == ProjectEventType.USER_MESSAGE and "blocked" in lower:
            task_id = ui_context.get("task_id")
            node_id = ui_context.get("architecture_node_id")
            task = next((t for t in context.tasks if task_id and t.id == task_id), None)
            if task is None and node_id:
                task = next((t for t in context.tasks if t.related_component == node_id), None)
            if task is None and ("stable listing" in lower or "listing verification" in lower):
                task = next((t for t in context.tasks if t.related_component == "listing_verification"), None)
            if task:
                return AgentDecision(
                    summary=f"{task.title} is blocked by the reported execution evidence. The accepted architecture still fits the problem, so no architecture change is proposed.",
                    actions=[AgentAction(type=AgentActionType.UPDATE_TASK, payload={"task_id": task.id, "changes": {"status": "BLOCKED", "description": task.description + " Blocked: external provider does not expose stable listing IDs."}})],
                )

        if event.type == ProjectEventType.USER_MESSAGE and ("without changing" in lower or "keep current" in lower):
            return AgentDecision(
                summary="KEEP_CURRENT: this can be handled inside the existing Listing Verification boundary with an internal mapping strategy; no accepted architecture boundary needs to change.",
                actions=[AgentAction(type=AgentActionType.NO_ACTION)],
            )

        if event.type == ProjectEventType.USER_MESSAGE and "cloud sql" in lower:
            proposal = ArchitectureChangeProposal(
                project_id=context.project.id,
                reason="Persistent favorites, history, reporting, and analytics now require relational joins that materially change the accepted persistence choice.",
                evidence=[message],
                observed_change="Primary persistent application data is moving from Firestore to relational storage.",
                affected_components=["data_state", "app_data_store"],
                proposed_changes=[{
                    "operation": "replace_component",
                    "component_id": "app_data_store",
                    "new_name": "Cloud SQL",
                    "new_type": "cloud_sql",
                    "new_kind": "DATA_STORE",
                    "new_responsibility": "Persist relational user, favorite, history, shortlist, and reporting data.",
                }],
                impact="Persistence implementation and related data tasks must be re-evaluated; short-lived Agent Session State can remain separate.",
                recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
            )
            return AgentDecision(
                summary="The new relational-query requirement crosses an accepted architecture boundary. Human review is required before changing Data & State.",
                actions=[AgentAction(type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE, payload={"proposal": proposal.model_dump(mode="json")})],
                architecture_review_required=True,
            )

        if event.type == ProjectEventType.USER_MESSAGE and ui_context.get("proposal_id"):
            proposal = next((p for p in context.pending_proposals if p.id == ui_context.get("proposal_id")), None)
            if proposal:
                affected_tasks = [
                    task.title
                    for task in context.tasks
                    if task.related_component in set(proposal.affected_components)
                ]
                task_text = ", ".join(affected_tasks) if affected_tasks else "no directly linked human task yet"
                return AgentDecision(
                    summary=(
                        f"This proposal affects {', '.join(proposal.affected_components)}. "
                        f"Related human work: {task_text}. The accepted architecture remains unchanged until you decide."
                    ),
                    actions=[AgentAction(type=AgentActionType.NO_ACTION)],
                )

        return AgentDecision(summary="No justified project-state mutation is needed.", actions=[AgentAction(type=AgentActionType.NO_ACTION)])


_demo_db = os.environ.get("ARCHBRO_DEMO_DB", "qa/manual_demo.db")
app = build_app(ProjectRepository(_demo_db), ManualDemoProvider())
