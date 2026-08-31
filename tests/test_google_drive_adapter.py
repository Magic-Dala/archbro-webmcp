import json

import archbro.integrations.google_drive as google_drive_module
from archbro.integrations.google_drive import GoogleDriveApiAdapter


class _Response:
    def __init__(self, payload, *, content_type="application/json"):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self._body if amount < 0 else self._body[:amount]


def test_google_drive_adapter_exposes_the_fixed_drive_tool_surface():
    adapter = GoogleDriveApiAdapter("drive-access-token")

    assert [tool["name"] for tool in adapter.list_tools()] == [
        "copy_file",
        "create_file",
        "download_file_content",
        "get_file_metadata",
        "get_file_permissions",
        "list_recent_files",
        "read_file_content",
        "search_files",
    ]


def test_google_drive_adapter_calls_drive_api_without_cloud_mcp_headers(monkeypatch):
    seen = []

    def fake_urlopen(request, *, timeout):
        seen.append((request, timeout))
        return _Response({"files": [{"id": "file-1", "name": "notes.txt"}]})

    monkeypatch.setattr(google_drive_module, "urlopen", fake_urlopen)
    adapter = GoogleDriveApiAdapter("drive-access-token")

    result = adapter.call_tool("list_recent_files", {"pageSize": 1})

    assert result.get("isError") is not True
    assert result["structuredContent"]["files"][0]["id"] == "file-1"
    request, timeout = seen[0]
    assert request.full_url.startswith("https://www.googleapis.com/drive/v3/files?")
    assert "drivemcp.googleapis.com" not in request.full_url
    assert "X-Goog-User-Project" not in request.headers
    assert timeout == 15.0


def test_google_drive_adapter_uses_upload_endpoint_for_file_content(monkeypatch):
    seen = []

    def fake_urlopen(request, *, timeout):
        seen.append(request)
        return _Response({"id": "file-2", "name": "hello.txt"})

    monkeypatch.setattr(google_drive_module, "urlopen", fake_urlopen)
    adapter = GoogleDriveApiAdapter("drive-access-token")

    result = adapter.call_tool(
        "create_file",
        {"title": "hello.txt", "textContent": "hello", "contentMimeType": "text/plain"},
    )

    assert result["structuredContent"]["id"] == "file-2"
    assert seen[0].full_url.startswith("https://www.googleapis.com/upload/drive/v3/files?")
