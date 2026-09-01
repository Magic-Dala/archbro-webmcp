from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from archbro.backend.core.authorization import TrustedPrincipal
from archbro.backend.mcp.provider_gateway import ExternalMcpGateway, McpConnectionConfig
from archbro.backend.mcp.provider_oauth import McpOAuthManager, OAuthSetupRequired


PrincipalFor = Callable[[Request], Awaitable[TrustedPrincipal]]


class McpToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def build_provider_mcp_router(principal_for: PrincipalFor) -> APIRouter:
    """Human-controlled MCP provider connections scoped to one trusted principal.

    This surface is intentionally separate from the project-scoped ConnectedMcpGateway
    used by WebMCP agents. Provider credentials and dynamic connections remain backend-
    only and memory-only; the browser never receives access or refresh tokens.
    """

    router = APIRouter()
    gateways: dict[str, ExternalMcpGateway] = {}
    oauth_managers: dict[str, McpOAuthManager] = {}
    oauth_state_owners: dict[str, str] = {}
    # Slack refuses a localhost redirect, and the OAuth popup has to post back
    # to the developer's UI origin, so both borrow fixed values during local
    # development. The condition is the environment itself: this used to be
    # inferred from the persistence backend being SQLite, which stopped meaning
    # anything once PostgreSQL became the only store.
    local_environment = os.getenv("ARCHBRO_ENV", "local").strip().lower() == "local"

    def runtime_for(principal: TrustedPrincipal) -> tuple[ExternalMcpGateway, McpOAuthManager]:
        user_id = principal.user_id
        gateway = gateways.get(user_id)
        if gateway is None:
            gateway = ExternalMcpGateway()
            gateways[user_id] = gateway
            oauth_managers[user_id] = McpOAuthManager(gateway)
        return gateway, oauth_managers[user_id]

    def runtime_for_owner(user_id: str) -> tuple[ExternalMcpGateway, McpOAuthManager] | None:
        gateway = gateways.get(user_id)
        manager = oauth_managers.get(user_id)
        if gateway is None or manager is None:
            return None
        return gateway, manager

    def oauth_redirect_uri(request: Request, provider_id: str) -> str:
        if provider_id == "slack" and local_environment:
            public_base = os.getenv(
                "ARCHBRO_SLACK_OAUTH_REDIRECT_BASE_URL",
                "https://archbro-dev.magicdala.com",
            ).strip().rstrip("/")
            parsed = urlsplit(public_base)
            if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.query and not parsed.fragment:
                return f"{public_base}/mcp/oauth/{provider_id}/callback"
        return f"{str(request.base_url).rstrip('/')}/mcp/oauth/{provider_id}/callback"

    def oauth_popup_response(
        provider_id: str,
        *,
        ok: bool,
        message: str,
        connection_id: str | None = None,
    ) -> HTMLResponse:
        payload = {
            "type": "archbro-mcp-oauth",
            "provider": provider_id,
            "ok": ok,
            "message": message[:300],
            "connectionId": connection_id,
        }
        payload_json = json.dumps(payload).replace("<", "\\u003c")
        target_origin = "window.location.origin"
        if provider_id == "slack" and local_environment:
            local_ui_origin = os.getenv(
                "ARCHBRO_LOCAL_UI_ORIGIN",
                "http://127.0.0.1:8012",
            ).strip().rstrip("/")
            parsed_ui_origin = urlsplit(local_ui_origin)
            if parsed_ui_origin.scheme in {"http", "https"} and parsed_ui_origin.netloc:
                target_origin = json.dumps(
                    f"{parsed_ui_origin.scheme}://{parsed_ui_origin.netloc}"
                ).replace("<", "\\u003c")
        title = "Connected" if ok else "Connection failed"
        icon = "✓" if ok else "!"
        body = f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>ArchBro MCP OAuth</title>
<style>body{{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:#f7f9fc;color:#172033;display:grid;place-items:center;min-height:100vh}}.card{{width:min(420px,calc(100vw - 36px));background:#fff;border:1px solid #e5eaf1;border-radius:20px;padding:30px;box-shadow:0 22px 60px rgba(15,23,42,.16);text-align:center}}.icon{{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;margin:0 auto 16px;font-size:25px;font-weight:900;background:{'#ecfdf5' if ok else '#fef2f2'};color:{'#15803d' if ok else '#b91c1c'}}}h1{{font-size:20px;margin:0}}p{{font-size:12px;line-height:1.6;color:#6b7788;margin:10px 0 0}}</style></head>
<body><div class=\"card\"><div class=\"icon\">{icon}</div><h1>{escape(title)}</h1><p>{escape(message)}</p><p>This window can close automatically.</p></div>
<script>const payload={payload_json};const targetOrigin={target_origin};if(window.opener){{window.opener.postMessage(payload,targetOrigin);}}setTimeout(()=>window.close(),650);</script></body></html>"""
        return HTMLResponse(body, status_code=200, headers={"Cache-Control": "no-store"})

    @router.get("/mcp/auth/github/status")
    async def get_github_auth_status(http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        connected = next(
            (
                connection
                for connection in gateway.list_connections()
                if connection.get("provider") == "github"
                and connection.get("auth_type") == "github_oauth"
                and connection.get("last_probe_ok") is True
                and not connection.get("authorization_pending")
            ),
            None,
        )
        configured = shutil.which("docker") is not None
        return {
            "provider": "github",
            "name": "GitHub",
            "configured": configured,
            "connected": connected is not None,
            "auth_method": "official_mcp_oauth",
            "endpoint": "GitHub official MCP · OAuth",
            "message": (
                "GitHub OAuth is connected."
                if connected
                else "Ready to open the official GitHub authorization window."
                if configured
                else "Docker is required for the official GitHub MCP OAuth runtime."
            ),
        }

    @router.post("/mcp/auth/github/start")
    async def start_github_authorization(http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.start_github_oauth_connection)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/mcp/auth/github/{connection_id}/poll")
    async def poll_github_authorization(connection_id: str, http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.poll_github_oauth, connection_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP connection not found")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/mcp/auth/google-drive/status")
    async def get_google_drive_auth_status(http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        connected = next(
            (
                connection
                for connection in gateway.list_connections()
                if connection.get("provider") == "google-drive"
                and connection.get("auth_type") in {"google_gcloud", "google_drive_oauth"}
                and connection.get("last_probe_ok") is True
                and not connection.get("authorization_pending")
            ),
            None,
        )
        gcloud = shutil.which("gcloud")
        readiness = gateway.google_drive_readiness(gcloud=gcloud)
        return {
            "provider": "google-drive",
            "name": "Google Drive",
            "configured": bool(gcloud),
            "connected": connected is not None,
            "auth_method": "google_drive_oauth",
            "endpoint": "Google Drive API · OAuth",
            "prerequisites": readiness,
            "message": (
                "Google Drive OAuth is connected."
                if connected
                else readiness["message"]
            ),
        }

    @router.post("/mcp/auth/google-drive/start")
    async def start_google_drive_authorization(http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.start_google_drive_oauth_connection)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/mcp/auth/google-drive/{connection_id}/poll")
    async def poll_google_drive_authorization(connection_id: str, http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.poll_google_drive_oauth, connection_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP connection not found")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/mcp/oauth/{provider_id}/status")
    async def get_mcp_oauth_status(provider_id: str, http_request: Request):
        principal = await principal_for(http_request)
        _, manager = runtime_for(principal)
        try:
            return manager.provider_status(
                provider_id,
                oauth_redirect_uri(http_request, provider_id),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP OAuth provider not found")

    @router.get("/mcp/oauth/{provider_id}/start")
    async def start_mcp_oauth(provider_id: str, http_request: Request):
        principal = await principal_for(http_request)
        _, manager = runtime_for(principal)
        try:
            authorization_url = manager.start(
                provider_id,
                oauth_redirect_uri(http_request, provider_id),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP OAuth provider not found")
        except OAuthSetupRequired:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message=(
                    f"{provider_id.replace('-', ' ').title()} sign-in is not provisioned in this ArchBro build. "
                    "The provider identity must be configured by the ArchBro deployment, not by the end user."
                ),
            )
        parsed_state = parse_qs(urlsplit(authorization_url).query).get("state", [])
        if not parsed_state:
            raise HTTPException(status_code=500, detail="OAuth provider did not return a state value")
        oauth_state_owners[parsed_state[0]] = principal.user_id
        return RedirectResponse(
            authorization_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/mcp/oauth/{provider_id}/callback", response_class=HTMLResponse)
    async def complete_mcp_oauth(
        provider_id: str,
        request: Request,
        state: str = "",
        code: str = "",
        error: str | None = None,
        error_description: str | None = None,
    ):
        owner_user_id = oauth_state_owners.pop(state, None) if state else None
        if error:
            message = error_description or error
            return oauth_popup_response(
                provider_id,
                ok=False,
                message=f"Authorization was not completed: {message}",
            )
        if not state or not code:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message="OAuth callback is missing state or authorization code.",
            )
        if not owner_user_id:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message="OAuth state is invalid or expired.",
            )
        runtime = runtime_for_owner(owner_user_id)
        if runtime is None:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message="OAuth session is no longer available.",
            )
        _, manager = runtime
        try:
            result = await asyncio.to_thread(
                manager.complete,
                provider_id,
                state=state,
                code=code,
                redirect_uri=oauth_redirect_uri(request, provider_id),
            )
        except KeyError:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message="Unsupported MCP OAuth provider.",
            )
        except OAuthSetupRequired:
            return oauth_popup_response(
                provider_id,
                ok=False,
                message="This provider identity is not provisioned in the current ArchBro deployment.",
            )
        except (ValueError, RuntimeError) as exc:
            return oauth_popup_response(provider_id, ok=False, message=str(exc))
        connection = result["connection"]
        return oauth_popup_response(
            provider_id,
            ok=True,
            message=f"{connection['name']} is connected to ArchBro.",
            connection_id=connection["id"],
        )

    @router.get("/mcp/connections")
    async def list_mcp_connections(http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        return gateway.list_connections()

    @router.post("/mcp/connections")
    async def add_mcp_connection(request: McpConnectionConfig, http_request: Request):
        principal = await principal_for(http_request)
        if not principal.local_development:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Custom browser-supplied MCP endpoints are disabled outside local development. "
                    "Use a built-in provider or deployment-configured project MCP source."
                ),
            )
        gateway, _ = runtime_for(principal)
        return gateway.add_connection(request)

    @router.delete("/mcp/connections/{connection_id}", status_code=204)
    async def remove_mcp_connection(connection_id: str, http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        if not gateway.remove_connection(connection_id):
            raise HTTPException(status_code=404, detail="MCP connection not found")
        return None

    @router.post("/mcp/connections/{connection_id}/probe")
    async def probe_mcp_connection(connection_id: str, http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.probe, connection_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP connection not found")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @router.get("/mcp/connections/{connection_id}/tools")
    async def list_mcp_tools(connection_id: str, http_request: Request):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(gateway.list_tools, connection_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP connection not found")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @router.post("/mcp/connections/{connection_id}/tools/{tool_name}")
    async def call_mcp_tool(
        connection_id: str,
        tool_name: str,
        request: McpToolCallRequest,
        http_request: Request,
    ):
        principal = await principal_for(http_request)
        gateway, _ = runtime_for(principal)
        try:
            return await asyncio.to_thread(
                gateway.call_tool,
                connection_id,
                tool_name,
                request.arguments,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="MCP connection not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    return router
