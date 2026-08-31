from __future__ import annotations

import base64
import binascii
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DRIVE_API_BASE_URL = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE_URL = "https://www.googleapis.com/upload/drive/v3"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_PAGE_SIZE = 100
MAX_CONTENT_BYTES = 8 * 1024 * 1024

_FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,webViewLink,parents,"
    "trashed,description,starred"
)
_NATIVE_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")


def _object_schema(
    *,
    properties: dict[str, dict[str, Any]] | None = None,
    required: tuple[str, ...] = (),
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    if required:
        schema["required"] = list(required)
    return schema


GOOGLE_DRIVE_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "copy_file",
        "description": "Request to copy a file.",
        "inputSchema": _object_schema(
            properties={
                "fileId": {"type": "string", "description": "Required. The ID of the file to copy."},
                "parentId": {"type": "string", "description": "The parent id of the newly created file."},
                "title": {"type": "string", "description": "The title of the newly created file."},
            },
            required=("fileId",),
        ),
    },
    {
        "name": "create_file",
        "description": "Request to upload a file.",
        "inputSchema": _object_schema(
            properties={
                "base64Content": {"type": "string", "description": "Optional base64 encoded content."},
                "content": {"type": "string", "description": "Deprecated base64 encoded content."},
                "contentMimeType": {"type": "string", "description": "MIME type of supplied content."},
                "disableConversionToGoogleType": {"type": "boolean"},
                "mimeType": {"type": "string", "description": "Deprecated content MIME type."},
                "parentId": {"type": "string"},
                "textContent": {"type": "string", "description": "Optional UTF-8 text content."},
                "title": {"type": "string", "description": "Required file title."},
            },
            required=("title",),
        ),
    },
    {
        "name": "download_file_content",
        "description": "Defines a request to download a file's content.",
        "inputSchema": _object_schema(
            properties={
                "exportMimeType": {"type": "string", "description": "Optional export MIME type for native files."},
                "fileId": {"type": "string", "description": "Required. The ID of the file to retrieve."},
            },
            required=("fileId",),
        ),
    },
    {
        "name": "get_file_metadata",
        "description": "Request to get the file.",
        "inputSchema": _object_schema(
            properties={
                "excludeContentSnippets": {"type": "boolean"},
                "fileId": {"type": "string", "description": "Required. The ID of the file to retrieve."},
            },
            required=("fileId",),
        ),
    },
    {
        "name": "get_file_permissions",
        "description": "Request to get file permissions.",
        "inputSchema": _object_schema(
            properties={"fileId": {"type": "string", "description": "Required. The ID of the file."}},
            required=("fileId",),
        ),
    },
    {
        "name": "list_recent_files",
        "description": "Request to list files.",
        "inputSchema": _object_schema(
            properties={
                "excludeContentSnippets": {"type": "boolean"},
                "orderBy": {"type": "string"},
                "pageSize": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE},
                "pageToken": {"type": "string"},
            }
        ),
    },
    {
        "name": "read_file_content",
        "description": "Request to read file content with support for fetching comments.",
        "inputSchema": _object_schema(
            properties={
                "fileId": {"type": "string", "description": "Required. The ID of the file."},
                "includeComments": {"type": "boolean"},
            },
            required=("fileId",),
        ),
    },
    {
        "name": "search_files",
        "description": "Request to search files.",
        "inputSchema": _object_schema(
            properties={
                "excludeContentSnippets": {"type": "boolean"},
                "pageSize": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE},
                "pageToken": {"type": "string"},
                "query": {"type": "string", "description": "The Google Drive search query."},
            }
        ),
    },
)


class GoogleDriveApiAdapter:
    """MCP-shaped adapter for user-delegated Google Drive REST access."""

    def __init__(self, access_token: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if not access_token.strip():
            raise ValueError("Google Drive access token is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._access_token = access_token.strip()
        self.timeout_seconds = timeout_seconds

    def update_access_token(self, access_token: str) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Google Drive access token is required")
        self._access_token = token

    def list_tools(self) -> list[dict[str, Any]]:
        return [json.loads(json.dumps(tool)) for tool in GOOGLE_DRIVE_TOOLS]

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        name = (tool_name or "").strip()
        args = arguments or {}
        handlers = {
            "copy_file": self._copy_file,
            "create_file": self._create_file,
            "download_file_content": self._download_file_content,
            "get_file_metadata": self._get_file_metadata,
            "get_file_permissions": self._get_file_permissions,
            "list_recent_files": self._list_recent_files,
            "read_file_content": self._read_file_content,
            "search_files": self._search_files,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ValueError(f"Google Drive tool not found: {name}")
        try:
            value = handler(args)
        except GoogleDriveApiError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "structuredContent": value,
        }

    def _list_recent_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("excludeContentSnippets", "orderBy", "pageSize", "pageToken"))
        params = self._list_params(arguments)
        params["q"] = "trashed = false"
        params["orderBy"] = self._order_by(arguments.get("orderBy"))
        return self._get_json("/files", params=params)

    def _search_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("excludeContentSnippets", "pageSize", "pageToken", "query"))
        params = self._list_params(arguments)
        query = str(arguments.get("query") or "").strip()
        params["q"] = f"({query}) and trashed = false" if query else "trashed = false"
        return self._get_json("/files", params=params)

    def _get_file_metadata(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("excludeContentSnippets", "fileId"))
        file_id = self._file_id(arguments)
        fields = _FILE_FIELDS
        if arguments.get("excludeContentSnippets"):
            fields = fields.replace(",description", "")
        return self._get_json(f"/files/{quote(file_id, safe='')}", params={"fields": fields})

    def _get_file_permissions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("fileId",))
        file_id = self._file_id(arguments)
        return self._get_json(
            f"/files/{quote(file_id, safe='')}/permissions",
            params={
                "fields": "nextPageToken,permissions(id,type,emailAddress,role,displayName,domain,allowFileDiscovery,expirationTime)"
            },
        )

    def _read_file_content(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("fileId", "includeComments"))
        file_id = self._file_id(arguments)
        metadata = self._get_file_metadata({"fileId": file_id})
        raw, content_mime_type = self._download_bytes(metadata)
        value: dict[str, Any] = {
            "fileId": file_id,
            "name": metadata.get("name"),
            "mimeType": metadata.get("mimeType"),
            "contentMimeType": content_mime_type,
            "content": raw.decode("utf-8", errors="replace"),
        }
        if arguments.get("includeComments"):
            value["comments"] = self._get_json(
                f"/files/{quote(file_id, safe='')}/comments",
                params={"fields": "comments(id,content,quotedFileContent,createdTime,modifiedTime,author(displayName,emailAddress),replies)"},
            ).get("comments", [])
        return value

    def _download_file_content(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("exportMimeType", "fileId"))
        file_id = self._file_id(arguments)
        metadata = self._get_file_metadata({"fileId": file_id})
        raw, content_mime_type = self._download_bytes(metadata, export_mime_type=arguments.get("exportMimeType"))
        value: dict[str, Any] = {
            "fileId": file_id,
            "name": metadata.get("name"),
            "mimeType": metadata.get("mimeType"),
            "contentMimeType": content_mime_type,
            "size": len(raw),
        }
        if self._is_text_mime(content_mime_type):
            value["textContent"] = raw.decode("utf-8", errors="replace")
        else:
            value["base64Content"] = base64.b64encode(raw).decode("ascii")
        return value

    def _copy_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(arguments, allowed=("fileId", "parentId", "title"))
        file_id = self._file_id(arguments)
        metadata: dict[str, Any] = {}
        title = self._optional_text(arguments.get("title"), "title", max_length=512)
        parent_id = self._optional_text(arguments.get("parentId"), "parentId", max_length=512)
        if title:
            metadata["name"] = title
        if parent_id:
            metadata["parents"] = [parent_id]
        return self._post_json(
            f"/files/{quote(file_id, safe='')}/copy",
            metadata,
            params={"fields": _FILE_FIELDS},
        )

    def _create_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown(
            arguments,
            allowed=(
                "base64Content",
                "content",
                "contentMimeType",
                "disableConversionToGoogleType",
                "mimeType",
                "parentId",
                "textContent",
                "title",
            ),
        )
        title = self._required_text(arguments.get("title"), "title", max_length=512)
        parent_id = self._optional_text(arguments.get("parentId"), "parentId", max_length=512)
        base64_content = self._optional_text(arguments.get("base64Content"), "base64Content", max_length=MAX_CONTENT_BYTES * 2)
        legacy_content = self._optional_text(arguments.get("content"), "content", max_length=MAX_CONTENT_BYTES * 2)
        text_content = arguments.get("textContent")
        if text_content is not None and not isinstance(text_content, str):
            raise ValueError("textContent must be a string")
        if base64_content and legacy_content:
            raise ValueError("content and base64Content cannot both be set")
        if base64_content and text_content is not None:
            raise ValueError("base64Content and textContent cannot both be set")
        if legacy_content:
            base64_content = legacy_content
        has_content = bool(base64_content) or text_content is not None
        content_mime_type = self._optional_text(arguments.get("contentMimeType"), "contentMimeType", max_length=256)
        legacy_mime_type = self._optional_text(arguments.get("mimeType"), "mimeType", max_length=256)
        if has_content and not content_mime_type:
            content_mime_type = legacy_mime_type
        if has_content and not content_mime_type:
            raise ValueError("contentMimeType is required when content is provided")

        metadata: dict[str, Any] = {"name": title}
        if parent_id:
            metadata["parents"] = [parent_id]
        if not has_content:
            if content_mime_type:
                metadata["mimeType"] = content_mime_type
            return self._post_json("/files", metadata, params={"fields": _FILE_FIELDS})

        if base64_content:
            try:
                raw = base64.b64decode(base64_content, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("base64Content is not valid base64") from None
        else:
            raw = str(text_content).encode("utf-8")
        if len(raw) > MAX_CONTENT_BYTES:
            raise ValueError(f"file content is limited to {MAX_CONTENT_BYTES} bytes")
        upload_mime_type = content_mime_type
        if not arguments.get("disableConversionToGoogleType"):
            conversion = {
                "text/plain": "application/vnd.google-apps.document",
                "text/csv": "application/vnd.google-apps.spreadsheet",
                "text/html": "application/vnd.google-apps.document",
            }
            metadata["mimeType"] = conversion.get(content_mime_type, content_mime_type)
        else:
            metadata["mimeType"] = content_mime_type
        return self._multipart_create(metadata, raw, upload_mime_type)

    def _download_bytes(
        self,
        metadata: dict[str, Any],
        *,
        export_mime_type: Any = None,
    ) -> tuple[bytes, str]:
        file_id = self._required_text(metadata.get("id"), "fileId", max_length=512)
        native_mime_type = str(metadata.get("mimeType") or "")
        if native_mime_type.startswith("application/vnd.google-apps."):
            export_type = self._optional_text(export_mime_type, "exportMimeType", max_length=256)
            export_type = export_type or _NATIVE_EXPORT_MIME_TYPES.get(native_mime_type, "text/plain")
            raw, returned_mime_type = self._request_bytes(
                "GET",
                f"/files/{quote(file_id, safe='')}/export",
                params={"mimeType": export_type},
            )
            return raw, returned_mime_type or export_type
        raw, returned_mime_type = self._request_bytes(
            "GET",
            f"/files/{quote(file_id, safe='')}",
            params={"alt": "media"},
        )
        return raw, returned_mime_type or native_mime_type or "application/octet-stream"

    def _list_params(self, arguments: dict[str, Any]) -> dict[str, str]:
        params = {
            "pageSize": str(self._page_size(arguments.get("pageSize"))),
            "spaces": "drive",
            "fields": f"nextPageToken,files({_FILE_FIELDS})",
        }
        page_token = self._optional_text(arguments.get("pageToken"), "pageToken", max_length=4096)
        if page_token:
            params["pageToken"] = page_token
        return params

    @staticmethod
    def _order_by(value: Any) -> str:
        order = str(value or "modifiedTime desc").strip()
        if not order or len(order) > 256:
            raise ValueError("orderBy must be a non-empty value shorter than 256 characters")
        return order

    @staticmethod
    def _page_size(value: Any) -> int:
        if value in (None, ""):
            return 20
        try:
            page_size = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"pageSize must be an integer between 1 and {MAX_PAGE_SIZE}") from None
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"pageSize must be an integer between 1 and {MAX_PAGE_SIZE}")
        return page_size

    @staticmethod
    def _file_id(arguments: dict[str, Any]) -> str:
        return GoogleDriveApiAdapter._required_text(arguments.get("fileId"), "fileId", max_length=512)

    @staticmethod
    def _required_text(value: Any, name: str, *, max_length: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} is required")
        if len(text) > max_length:
            raise ValueError(f"{name} is too long")
        return text

    @staticmethod
    def _optional_text(value: Any, name: str, *, max_length: int) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        text = value.strip()
        if len(text) > max_length:
            raise ValueError(f"{name} is too long")
        return text or None

    @staticmethod
    def _reject_unknown(arguments: dict[str, Any], *, allowed: tuple[str, ...]) -> None:
        unknown = sorted(set(arguments) - set(allowed))
        if unknown:
            raise ValueError(f"unsupported Google Drive argument: {unknown[0]}")

    @staticmethod
    def _is_text_mime(mime_type: str) -> bool:
        return mime_type.startswith(_TEXT_MIME_PREFIXES)

    def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        raw, _ = self._request_bytes("GET", path, params=params)
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise GoogleDriveApiError("Google Drive returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise GoogleDriveApiError("Google Drive returned an invalid response")
        return payload

    def _post_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raw, _ = self._request_bytes(
            "POST",
            path,
            params=params,
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise GoogleDriveApiError("Google Drive returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise GoogleDriveApiError("Google Drive returned an invalid response")
        return payload

    def _multipart_create(self, metadata: dict[str, Any], raw: bytes, content_mime_type: str) -> dict[str, Any]:
        boundary = "archbro-drive-boundary"
        metadata_part = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
        body = b"".join(
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("ascii"),
                metadata_part,
                f"\r\n--{boundary}\r\nContent-Type: {content_mime_type}\r\n\r\n".encode("ascii"),
                raw,
                f"\r\n--{boundary}--\r\n".encode("ascii"),
            )
        )
        response, _ = self._request_bytes(
            "POST",
            "/files",
            params={"uploadType": "multipart", "fields": _FILE_FIELDS},
            body=body,
            content_type=f"multipart/related; boundary={boundary}",
            base_url=DRIVE_UPLOAD_BASE_URL,
        )
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            raise GoogleDriveApiError("Google Drive returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise GoogleDriveApiError("Google Drive returned an invalid response")
        return payload

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        base_url: str = DRIVE_API_BASE_URL,
    ) -> tuple[bytes, str]:
        url = f"{base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": "ArchBro/0.1",
        }
        if body is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read(MAX_CONTENT_BYTES + 1)
                if len(content) > MAX_CONTENT_BYTES:
                    raise GoogleDriveApiError(f"Google Drive response exceeds {MAX_CONTENT_BYTES} bytes")
                return content, response.headers.get("Content-Type", "")
        except GoogleDriveApiError:
            raise
        except HTTPError as exc:
            raise GoogleDriveApiError(self._http_error_message(exc)) from None
        except URLError as exc:
            raise GoogleDriveApiError(f"Google Drive request failed: {exc.reason}") from None

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        try:
            raw = exc.read(4096).decode("utf-8", errors="replace")
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "request rejected").strip()
                return f"Google Drive request failed with HTTP {exc.code}: {message[:300]}"
        return f"Google Drive request failed with HTTP {exc.code}"


class GoogleDriveApiError(RuntimeError):
    pass
