from __future__ import annotations

from skillrunner.mcp.client import MCPClient


class MCPOrchestrator:
    def __init__(self, client: MCPClient | None = None) -> None:
        self.client = client or MCPClient()

    async def validate_required_tools(self, required_tools: list[str] | None) -> list[str]:
        if not required_tools:
            return []
        available = {tool.name for tool in await self.client.list_tools()}
        return [tool for tool in required_tools if tool not in available]
