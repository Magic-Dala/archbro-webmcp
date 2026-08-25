import asyncio
import os
from pathlib import Path
import tempfile

import pytest
from dotenv import load_dotenv

from archbro.backend.agent.orchestration import AgentOrchestrator
from archbro.backend.core.contracts import Architecture, Project, ProjectEvent, ProjectEventSource, ProjectEventType
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
    names = {component.name.lower() for component in architecture.components}
    assert any("react" in name for name in names)
    assert any("fastapi" in name for name in names)
    assert any("postgres" in name for name in names)
    assert repo.list_tasks(project.id)
    assert repo.list_proposals(project.id) == []
