from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from archbro.backend.api.routes import build_router
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.gemini import GeminiProvider
from archbro.backend.llm.provider import ModelProvider
from archbro.platform.persistence.repository import ProjectRepository

load_dotenv()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def create_app(
    repository: ProjectRepositoryPort | None = None,
    provider: ModelProvider | None = None,
    *,
    web_dir: str | Path | None = None,
) -> FastAPI:
    """Max-owned runtime composition root.

    This is the only layer that chooses concrete persistence/model providers and
    binds Shaun's frontend to Jim's API. Backend modules stay independent of
    deployment/runtime details.
    """

    selected_repository = repository
    if selected_repository is None:
        persistence_mode = os.getenv("ARCHBRO_PERSISTENCE", "sqlite").strip().lower()
        if persistence_mode == "sqlite":
            db_path = os.getenv("ARCHBRO_DB") or os.getenv("HUMAN_AGENT_DB") or "archbro.db"
            selected_repository = ProjectRepository(db_path)
        elif persistence_mode == "firestore":
            from archbro.integrations.firebase.admin import get_firestore_client
            from archbro.platform.persistence.firestore import FirestoreProjectRepository

            project_id = (
                os.getenv("FIRESTORE_PROJECT_ID")
                or os.getenv("FIREBASE_PROJECT_ID")
                or os.getenv("GOOGLE_CLOUD_PROJECT")
                or ""
            ).strip()
            if not project_id:
                raise ValueError(
                    "FIRESTORE_PROJECT_ID, FIREBASE_PROJECT_ID, or GOOGLE_CLOUD_PROJECT "
                    "is required when ARCHBRO_PERSISTENCE=firestore"
                )
            database_id = os.getenv("FIRESTORE_DATABASE_ID", "(default)").strip() or "(default)"
            prefix = os.getenv("ARCHBRO_FIRESTORE_PREFIX", "archbro").strip() or "archbro"
            selected_repository = FirestoreProjectRepository(
                get_firestore_client(project_id, database_id),
                collection_prefix=prefix,
            )
        else:
            raise ValueError("ARCHBRO_PERSISTENCE must be 'sqlite' or 'firestore'")

    selected_provider = provider
    if selected_provider is None:
        provider_name = (os.getenv("ARCHBRO_PROVIDER") or os.getenv("HUMAN_AGENT_PROVIDER") or "gemini").lower()
        selected_provider = (
            FakeModelProvider()
            if provider_name == "fake"
            else GeminiProvider(model_id=os.getenv("GEMINI_MODEL", "gemini-3.7-flash"))
        )

    goal_timeout = float(
        os.getenv("ARCHBRO_GOAL_REQUEST_TIMEOUT_SECONDS")
        or os.getenv("HUMAN_AGENT_GOAL_REQUEST_TIMEOUT_SECONDS")
        or "30"
    )
    if goal_timeout <= 0:
        raise ValueError("ARCHBRO_GOAL_REQUEST_TIMEOUT_SECONDS must be greater than zero")

    frontend_dir = Path(web_dir) if web_dir is not None else _project_root() / "frontend" / "web"
    if not frontend_dir.exists():
        raise RuntimeError(f"Archbro frontend directory not found: {frontend_dir}")

    app = FastAPI(title="Archbro")
    app.include_router(
        build_router(
            selected_repository,
            selected_provider,
            goal_request_timeout_seconds=goal_timeout,
        )
    )

    @app.get("/", include_in_schema=False)
    async def web_app():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    return app


# Kept as a tiny compatibility alias for tests and QA harnesses.
build_app = create_app
