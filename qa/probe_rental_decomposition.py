import asyncio
import json

from dotenv import load_dotenv

from archbro.backend.core.contracts import Architecture, Project, ProjectContext, ProjectEvent, ProjectEventType
from archbro.backend.llm.gemini import GeminiProvider


GOAL = """建立一個基於 Google Cloud ADK 的 Agentic 租屋網站應用系統，整合 Google 全餐後端、使用者資料庫，以及對應的前端介面，讓 Agent 能夠協助使用者進行租屋相關的搜尋、推薦與互動任務。V0 應該能讓使用者在前端提出租屋需求，由 Agent 協調搜尋與推薦流程，讀取或保存必要的使用者與租屋資料，並回傳可理解的租屋結果。"""


async def main() -> None:
    load_dotenv()
    project = Project(name="Google Cloud Agentic Rental Platform QA", goal=GOAL)
    context = ProjectContext(
        project=project,
        architecture=Architecture(),
        tasks=[],
        pending_proposals=[],
    )
    event = ProjectEvent(
        project_id=project.id,
        type=ProjectEventType.USER_MESSAGE,
        payload={"intent": "INITIAL_ARCHITECTURE", "message": GOAL},
    )
    provider = GeminiProvider()
    decision = await provider.generate(event=event, context=context, system_prompt="unused")
    note_action = next(action for action in decision.actions if action.type.value == "ADD_PROJECT_NOTE")
    architecture = Architecture.model_validate_json(note_action.payload["note"].removeprefix("INITIAL_ARCHITECTURE:"))
    tasks = [action.payload["task"] for action in decision.actions if action.type.value == "CREATE_TASK"]
    print("MODEL", provider.last_model_id)
    print("COMPONENT_COUNT", len(architecture.components))
    print("COMPONENTS", json.dumps([component.model_dump(mode="json") for component in architecture.components], ensure_ascii=False))
    print("RELATIONSHIPS", json.dumps([relationship.model_dump(mode="json") for relationship in architecture.relationships], ensure_ascii=False))
    print("TASKS", json.dumps(tasks, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
