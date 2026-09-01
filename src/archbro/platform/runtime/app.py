from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from archbro.backend.api.routes import build_router
from archbro.backend.core.authorization import PrincipalProvider
from archbro.backend.core.repository import ProjectRepositoryPort
from archbro.backend.llm.fake import FakeModelProvider
from archbro.backend.llm.gemini import GeminiProvider
from archbro.backend.llm.provider import ModelProvider
from archbro.platform.persistence.postgres import PostgresProjectRepository

load_dotenv()

WEBMCP_SURFACE_VERSION = "archbro.semantic-webmcp.v3"
WEBMCP_DEFAULT_TOOL_COUNT = 14
WEBMCP_GATEWAY_TOOL_COUNT = 3


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def create_app(
    repository: ProjectRepositoryPort | None = None,
    provider: ModelProvider | None = None,
    *,
    web_dir: str | Path | None = None,
    principal_provider: PrincipalProvider | None = None,
) -> FastAPI:
    """Max-owned runtime composition root.

    This is the only layer that chooses concrete persistence/model providers and
    binds Shaun's frontend to Jim's API. Backend modules stay independent of
    deployment/runtime details.
    """

    selected_repository = repository
    if selected_repository is None:
        persistence_mode = os.getenv("ARCHBRO_PERSISTENCE", "postgres").strip().lower()
        if persistence_mode != "postgres":
            raise ValueError("ARCHBRO_PERSISTENCE must be 'postgres'")

        database_url = (os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            raise ValueError(
                "DATABASE_URL is required when ARCHBRO_PERSISTENCE=postgres"
            )
        selected_repository = PostgresProjectRepository(database_url)

    environment = os.getenv("ARCHBRO_ENV", "local").strip().lower()
    if environment not in {"local", "test", "production"}:
        raise ValueError("ARCHBRO_ENV must be 'local', 'test', or 'production'")

    edge_guard_mode = os.getenv("ARCHBRO_EDGE_GUARD", "off").strip().lower()
    if edge_guard_mode not in {"off", "required"}:
        raise ValueError("ARCHBRO_EDGE_GUARD must be 'off' or 'required'")
    edge_token = os.getenv("ARCHBRO_EDGE_TOKEN", "").strip()
    if edge_guard_mode == "required" and not edge_token:
        raise ValueError("ARCHBRO_EDGE_TOKEN is required when ARCHBRO_EDGE_GUARD=required")

    auth_mode = os.getenv("ARCHBRO_AUTH_MODE", "local").strip().lower()
    if auth_mode not in {"local", "firebase"}:
        raise ValueError("ARCHBRO_AUTH_MODE must be 'local' or 'firebase'")

    selected_principal_provider = principal_provider
    firebase_project_id = (
        os.getenv("FIREBASE_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()
    if selected_principal_provider is None:
        if auth_mode == "firebase":
            if not firebase_project_id:
                raise ValueError(
                    "FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required when "
                    "ARCHBRO_AUTH_MODE=firebase"
                )
            from archbro.integrations.firebase import FirebasePrincipalProvider

            selected_principal_provider = FirebasePrincipalProvider(firebase_project_id)
        elif environment == "production":
            raise ValueError(
                "Production Archbro must use ARCHBRO_AUTH_MODE=firebase; "
                "the local development principal is disabled in production."
            )

    public_firebase_config: dict[str, str] | None = None
    if auth_mode == "firebase":
        public_firebase_config = {
            "apiKey": os.getenv("ARCHBRO_FIREBASE_API_KEY", "").strip(),
            "authDomain": os.getenv("ARCHBRO_FIREBASE_AUTH_DOMAIN", "").strip(),
            "projectId": firebase_project_id,
            "appId": os.getenv("ARCHBRO_FIREBASE_APP_ID", "").strip(),
        }
        # Anonymous Firebase/Identity Platform auth only requires the project API
        # key and project id. authDomain/appId are optional until redirect-based
        # providers are enabled; keeping them optional avoids coupling production
        # auth to Firebase Hosting or a Firebase Management WebApp resource.
        required_public_keys = ("apiKey", "projectId")
        missing = [key for key in required_public_keys if not public_firebase_config[key]]
        if missing and environment == "production":
            raise ValueError(
                "Production Firebase browser configuration is incomplete: "
                + ", ".join(missing)
            )

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
    webmcp_asset_sha256 = hashlib.sha256(
        (frontend_dir / "archbro-webmcp.js").read_bytes()
    ).hexdigest()

    def connected_mcp_gateway_configured() -> bool:
        raw_connected_mcp = os.getenv("ARCHBRO_MCP_SERVERS_JSON", "").strip()
        if not raw_connected_mcp:
            return False
        try:
            return bool(json.loads(raw_connected_mcp))
        except (json.JSONDecodeError, TypeError):
            return False

    def webmcp_manifest_payload() -> dict[str, object]:
        gateway_configured = connected_mcp_gateway_configured()
        return {
            "surface": "archbro-webmcp",
            "surface_version": WEBMCP_SURFACE_VERSION,
            "asset_sha256": webmcp_asset_sha256,
            "connected_mcp_gateway_configured": gateway_configured,
            "expected_tool_count": (
                WEBMCP_DEFAULT_TOOL_COUNT + WEBMCP_GATEWAY_TOOL_COUNT
                if gateway_configured
                else WEBMCP_DEFAULT_TOOL_COUNT
            ),
        }

    app = FastAPI(title="Archbro")
    app.include_router(
        build_router(
            selected_repository,
            selected_provider,
            goal_request_timeout_seconds=goal_timeout,
            principal_provider=selected_principal_provider,
        )
    )

    @app.middleware("http")
    async def edge_origin_guard(request, call_next):
        # The container liveness probe runs inside the container and therefore
        # cannot present the edge token. Exempting it is safe because /healthz
        # discloses nothing beyond "this process is serving"; without the
        # exemption the probe gets 403, the container never reports healthy, and
        # a rolling deploy stalls.
        if edge_guard_mode == "required" and request.url.path != "/healthz":
            presented = request.headers.get("X-ArchBro-Edge-Token", "")
            if not presented or not hmac.compare_digest(presented, edge_token):
                return JSONResponse(status_code=403, content={"detail": "direct origin access is forbidden"})
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://www.gstatic.com; "
            "style-src 'self'; img-src 'self' data:; "
            "connect-src 'self' https://identitytoolkit.googleapis.com "
            "https://securetoken.googleapis.com https://www.googleapis.com; "
            "frame-src https://*.firebaseapp.com; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        if environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=86400"
        if request.url.path in {
            "/",
            "/runtime-config.js",
            "/webmcp-manifest.json",
            "/static/app.js",
            "/static/archbro-webmcp.js",
        }:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # Container liveness probe. Deliberately does not touch persistence: a probe
    # that fails during a transient database outage makes the orchestrator
    # restart the app while the database is still recovering, turning a short
    # outage into a restart storm. The database reports its own readiness.
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def web_app():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/webmcp-manifest.json", include_in_schema=False)
    async def webmcp_manifest():
        return JSONResponse(
            content=webmcp_manifest_payload(),
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/runtime-config.js", include_in_schema=False)
    async def runtime_config():
        manifest = webmcp_manifest_payload()
        payload = {
            "auth_mode": auth_mode,
            "firebase": public_firebase_config,
            "connected_mcp_gateway_configured": manifest["connected_mcp_gateway_configured"],
            "webmcp_surface_version": manifest["surface_version"],
            "webmcp_asset_sha256": manifest["asset_sha256"],
            "webmcp_expected_tool_count": manifest["expected_tool_count"],
            "webmcp_manifest_url": "/webmcp-manifest.json",
        }
        return Response(
            content="window.__ARCHBRO_RUNTIME_CONFIG__ = " + json.dumps(payload) + ";\n",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    return app


# Kept as a tiny compatibility alias for tests and QA harnesses.
build_app = create_app
