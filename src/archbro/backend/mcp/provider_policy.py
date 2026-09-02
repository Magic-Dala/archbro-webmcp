from __future__ import annotations

from typing import Any

from archbro.backend.mcp.provider_gateway import ExternalMcpGateway


class ReadOnlyExternalMcpGateway(ExternalMcpGateway):
    """Provider gateway with an ArchBro-owned GitHub read-only policy.

    GitHub's official MCP readonly header/runtime mode remains the first barrier.
    This wrapper is the local fail-closed backstop: only tools that GitHub marks
    explicitly with MCP ``annotations.readOnlyHint=true`` are exposed or called.
    A missing annotation is treated as non-read-only.
    """

    @staticmethod
    def _tool_is_explicitly_read_only(tool: Any) -> bool:
        if not isinstance(tool, dict):
            return False
        annotations = tool.get("annotations")
        return isinstance(annotations, dict) and annotations.get("readOnlyHint") is True

    def _github_tool_is_allowed(self, state: Any, tool_name: str) -> bool:
        for tool in self._state_list_tools(state):
            if not isinstance(tool, dict) or str(tool.get("name") or "").strip() != tool_name:
                continue
            return self._tool_is_explicitly_read_only(tool)
        return False

    def list_tools(self, connection_id: str) -> dict[str, Any]:
        result = super().list_tools(connection_id)
        state = self._get(connection_id)
        if state.provider != "github":
            return result

        tools = [
            tool
            for tool in result["tools"]
            if self._tool_is_explicitly_read_only(tool)
        ]
        result["tools"] = tools
        result["tool_count"] = len(tools)
        state.tool_count = len(tools)
        return result

    def call_tool(
        self,
        connection_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._get(connection_id)
        name = (tool_name or "").strip()
        if not name:
            raise ValueError("tool_name is required")

        if state.provider == "github":
            self._ensure_fresh_oauth(state)
            if not self._github_tool_is_allowed(state, name):
                raise ValueError(
                    f"GitHub MCP tool {name!r} is not explicitly marked read-only; call blocked by ArchBro"
                )

        return super().call_tool(connection_id, name, arguments)
