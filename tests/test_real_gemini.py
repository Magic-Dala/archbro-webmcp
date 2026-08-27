import asyncio
import os
from pathlib import Path
import tempfile

import pytest
from dotenv import load_dotenv

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import (
    Architecture,
    Component,
    Project,
    ProjectEvent,
    ProjectEventSource,
    ProjectEventType,
)
from archbro.backend.core.evaluation import DriftClassification, DriftRecommendedAction
from archbro.backend.llm.gemini import GeminiProvider
from archbro.backend.llm.provider import GoalConversationMessage
from archbro.platform.persistence.repository import ProjectRepository

load_dotenv()


@pytest.mark.skipif(not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")), reason="Gemini API key not set")
def test_real_gemini_goal_and_ask_merge_to_architecture_flow():
    provider = GeminiProvider(model_id=os.getenv("GEMINI_TEST_MODEL", "gemini-3.5-flash-lite"))
    current_goal = (
        "Build a simple issue tracking system for a small software engineering team. "
        "Users need to create, view, edit, and change issue status between TODO, IN_PROGRESS, and DONE. "
        "For V0 use React for the frontend, FastAPI for the backend, and PostgreSQL for persistence through a REST API. "
        "The first usable milestone must run locally, persist issues across refreshes, and avoid microservices, queues, or Kubernetes."
    )
    narrow_ask = "Also let users export the current issue list to CSV. Keep the rest of the goal unchanged."
    draft = asyncio.run(provider.draft_goal(
        current_goal=current_goal,
        messages=[GoalConversationMessage(role="user", content=narrow_ask)],
    ))

    assert draft.goal
    assert draft.suggested_project_name
    assert draft.assistant_message
    lowered_goal = draft.goal.lower()
    assert "react" in lowered_goal
    assert "fastapi" in lowered_goal
    assert "postgres" in lowered_goal
    assert "csv" in lowered_goal
    assert "microservice" in lowered_goal

    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "gemini.db"))
    project = Project(
        name=draft.suggested_project_name,
        goal=draft.goal,
        description="Goal combined from direct editing and the pre-project Ask conversation.",
    )
    repo.save_project(project)
    repo.save_architecture(project.id, Architecture())
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        source=ProjectEventSource.FRONTEND,
        payload={"intent": "INITIAL_ARCHITECTURE"},
    )
    result = asyncio.run(AgentOrchestrator(repo, provider).observe_event(event))

    assert result.provider == "gemini"
    assert result.result == "SUCCESS", result.error
    architecture = repo.get_architecture(project.id)
    assert architecture.version == 1
    architecture_text = architecture.model_dump_json().lower()
    assert "react" in architecture_text
    assert "fastapi" in architecture_text
    assert "postgres" in architecture_text
    assert repo.list_tasks(project.id)
    assert repo.list_proposals(project.id) == []


@pytest.mark.skipif(not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")), reason="Gemini API key not set")
def test_real_gemini_drift_evaluation_proposes_without_mutating_accepted_architecture():
    repo = ProjectRepository(str(Path(tempfile.mkdtemp()) / "m4-real.db"))
    project = Project(
        name="M4 Drift Probe",
        goal="Build a project workspace with FastAPI and PostgreSQL persistence.",
        architecture_version=1,
    )
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            summary="Accepted V1",
            components=[
                Component(id="backend", name="FastAPI Backend", type="backend", responsibility="Serve project APIs."),
                Component(id="database", name="PostgreSQL", type="database", responsibility="Persist project state."),
            ],
        ),
    )
    provider = GeminiProvider(model_id=os.getenv("GEMINI_TEST_MODEL", "gemini-3.5-flash-lite"))
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        source=ProjectEventSource.HUMAN,
        payload={
            "message": (
                "The team has explicitly decided to replace PostgreSQL with Firestore as the primary persistence layer. "
                "This is a new accepted technical requirement, not just an implementation workaround."
            )
        },
    )

    result = asyncio.run(AgentOrchestrator(repo, provider).observe_event(event))

    assert result.result == "SUCCESS", result.error
    assert result.evaluation is not None
    assert result.evaluation.classification == DriftClassification.ARCHITECTURE_DRIFT
    assert result.evaluation.recommended_action == DriftRecommendedAction.PROPOSE_ARCHITECTURE_CHANGE
    assert result.evaluation.architecture_change_required is True
    assert len(result.proposal_ids) == 1
    assert repo.get_architecture(project.id).version == 1
    assert repo.get_architecture(project.id).find_component("database").name == "PostgreSQL"
