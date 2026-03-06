from __future__ import annotations

from typing import Any

from skillrunner.mcp.models import MCPTool, MCPToolResult


class MCPClient:
    async def list_tools(self) -> list[MCPTool]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(tool_name=name, content={"echo": arguments}, ok=True)
