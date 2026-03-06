from __future__ import annotations

from typing import Any

from skillrunner.mcp.orchestrator import MCPOrchestrator


class ToolExecutor:
    def __init__(self, orchestrator: MCPOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or MCPOrchestrator()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.orchestrator.client.call_tool(name, arguments)
        return {"tool": name, "ok": result.ok, "content": result.content}
