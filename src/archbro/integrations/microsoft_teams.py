from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_PAGE_SIZE = 50


def _object_schema(*, properties: dict[str, dict[str, Any]] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


TEAMS_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "teams_list_teams",
        "description": "List the Microsoft Teams that the signed-in user belongs to.",
        "inputSchema": _object_schema(),
    },
    {
        "name": "teams_list_channels",
        "description": "List channels visible to the signed-in user in a Microsoft Team.",
        "inputSchema": _object_schema(
            properties={"team_id": {"type": "string", "description": "The opaque Microsoft Graph team ID."}},
            required=("team_id",),
        ),
    },
    {
        "name": "teams_list_chats",
        "description": "List chats that the signed-in work or school account belongs to.",
        "inputSchema": _object_schema(
            properties={"top": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE}},
        ),
    },
    {
        "name": "teams_list_channel_messages",
        "description": "List root messages in a Microsoft Teams channel.",
        "inputSchema": _object_schema(
            properties={
                "team_id": {"type": "string", "description": "The opaque Microsoft Graph team ID."},
                "channel_id": {"type": "string", "description": "The opaque Microsoft Graph channel ID."},
                "top": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE},
            },
            required=("team_id", "channel_id"),
        ),
    },
    {
        "name": "teams_list_chat_messages",
        "description": "List messages in an existing Microsoft Teams chat.",
        "inputSchema": _object_schema(
            properties={
                "chat_id": {"type": "string", "description": "The opaque Microsoft Graph chat ID."},
                "top": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE},
            },
            required=("chat_id",),
        ),
    },
    {
        "name": "teams_send_chat_message",
        "description": "Send a plain-text message to an existing Microsoft Teams chat.",
        "inputSchema": _object_schema(
            properties={
                "chat_id": {"type": "string", "description": "The opaque Microsoft Graph chat ID."},
                "content": {"type": "string", "minLength": 1, "description": "Plain-text message content."},
            },
            required=("chat_id", "content"),
        ),
    },
    {
        "name": "teams_send_channel_message",
        "description": "Send a plain-text root message to a Microsoft Teams channel.",
        "inputSchema": _object_schema(
            properties={
                "team_id": {"type": "string", "description": "The opaque Microsoft Graph team ID."},
                "channel_id": {"type": "string", "description": "The opaque Microsoft Graph channel ID."},
                "content": {"type": "string", "minLength": 1, "description": "Plain-text message content."},
            },
            required=("team_id", "channel_id", "content"),
        ),
    },
)

TEAMS_WRITE_TOOL_NAMES = frozenset({
    "teams_send_chat_message",
    "teams_send_channel_message",
})


class MicrosoftTeamsGraphAdapter:
    """Small MCP-shaped adapter for user-delegated Microsoft Teams Graph calls."""

    def __init__(self, access_token: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, enable_write: bool = False) -> None:
        if not access_token.strip():
            raise ValueError("Microsoft Teams access token is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._access_token = access_token.strip()
        self.timeout_seconds = timeout_seconds
        self.enable_write = bool(enable_write)

    def update_access_token(self, access_token: str) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Microsoft Teams access token is required")
        self._access_token = token

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            json.loads(json.dumps(tool))
            for tool in TEAMS_TOOLS
            if self.enable_write or tool.get("name") not in TEAMS_WRITE_TOOL_NAMES
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        name = (tool_name or "").strip()
        if name in TEAMS_WRITE_TOOL_NAMES and not self.enable_write:
            raise ValueError("Microsoft Teams write tools are disabled; set ARCHBRO_TEAMS_ENABLE_WRITE=true to enable them")
        args = arguments or {}
        handlers = {
            "teams_list_teams": self._list_teams,
            "teams_list_channels": self._list_channels,
            "teams_list_chats": self._list_chats,
            "teams_list_channel_messages": self._list_channel_messages,
            "teams_list_chat_messages": self._list_chat_messages,
            "teams_send_chat_message": self._send_chat_message,
            "teams_send_channel_message": self._send_channel_message,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ValueError(f"Microsoft Teams tool not found: {name}")
        value = handler(args)
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "structuredContent": value,
        }

    def _list_teams(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=())
        return self._get("/me/joinedTeams")

    def _list_channels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("team_id", "top"))
        team_id = self._identifier(arguments, "team_id")
        return self._get(f"/teams/{team_id}/channels", top=self._top(arguments.get("top")))

    def _list_chats(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("top",))
        return self._get("/me/chats", top=self._top(arguments.get("top")))

    def _list_channel_messages(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("team_id", "channel_id", "top"))
        team_id = self._identifier(arguments, "team_id")
        channel_id = self._identifier(arguments, "channel_id")
        return self._get(
            f"/teams/{team_id}/channels/{channel_id}/messages",
            top=self._top(arguments.get("top")),
        )

    def _list_chat_messages(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("chat_id", "top"))
        chat_id = self._identifier(arguments, "chat_id")
        return self._get(f"/chats/{chat_id}/messages", top=self._top(arguments.get("top")))

    def _send_chat_message(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("chat_id", "content"))
        chat_id = self._identifier(arguments, "chat_id")
        content = self._content(arguments)
        return self._post(f"/chats/{chat_id}/messages", {"body": {"content": content}})

    def _send_channel_message(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("team_id", "channel_id", "content"))
        team_id = self._identifier(arguments, "team_id")
        channel_id = self._identifier(arguments, "channel_id")
        content = self._content(arguments)
        return self._post(
            f"/teams/{team_id}/channels/{channel_id}/messages",
            {"body": {"content": content}},
        )

    @staticmethod
    def _reject_unknown(arguments: dict[str, Any], *, allowed: tuple[str, ...]) -> None:
        unknown = sorted(set(arguments) - set(allowed))
        if unknown:
            raise ValueError(f"unsupported Microsoft Teams argument: {unknown[0]}")

    @staticmethod
    def _identifier(arguments: dict[str, Any], name: str) -> str:
        value = str(arguments.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is required")
        if len(value) > 512:
            raise ValueError(f"{name} is too long")
        return quote(value, safe="")

    @staticmethod
    def _content(arguments: dict[str, Any]) -> str:
        value = str(arguments.get("content") or "").strip()
        if not value:
            raise ValueError("content is required")
        if len(value) > 10000:
            raise ValueError("content is too long")
        return value

    @staticmethod
    def _top(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            top = int(value)
        except (TypeError, ValueError):
            raise ValueError("top must be an integer between 1 and 50") from None
        if not 1 <= top <= MAX_PAGE_SIZE:
            raise ValueError("top must be an integer between 1 and 50")
        return top

    def _get(self, path: str, *, top: int | None = None) -> dict[str, Any]:
        query = {"$top": str(top)} if top is not None else None
        return self._request("GET", path, query=query)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, body=body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{GRAPH_BASE_URL}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": "ArchBro/0.1",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise MicrosoftTeamsGraphError(self._http_error_message(exc)) from None
        except URLError as exc:
            raise MicrosoftTeamsGraphError(f"Microsoft Graph request failed: {exc.reason}") from None
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise MicrosoftTeamsGraphError("Microsoft Graph returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise MicrosoftTeamsGraphError("Microsoft Graph returned an invalid response")
        if payload.get("error"):
            raise MicrosoftTeamsGraphError(self._graph_error_message(payload))
        return payload

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            return MicrosoftTeamsGraphAdapter._graph_error_message(payload, status=exc.code)
        return f"Microsoft Graph request failed with HTTP {exc.code}"

    @staticmethod
    def _graph_error_message(payload: dict[str, Any], *, status: int | None = None) -> str:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        safe_message = str(message or "Microsoft Graph rejected the request").strip()[:300]
        return f"Microsoft Graph request failed{f' with HTTP {status}' if status else ''}: {safe_message}"


class MicrosoftTeamsGraphError(RuntimeError):
    pass
