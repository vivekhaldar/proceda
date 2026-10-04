"""ABOUTME: Bridges LLM tool calls to the MCP orchestrator.
ABOUTME: Executes tool calls, emits runtime events for each call/result, and handles errors."""

from __future__ import annotations

import logging
from typing import Any

from proceda.events import EventType, RunEvent
from proceda.mcp.orchestrator import MCPOrchestrator
from proceda.session import ToolCall

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tool calls through the MCP orchestrator and emits events."""

    def __init__(self, orchestrator: MCPOrchestrator, run_id: str) -> None:
        self._orchestrator = orchestrator
        self._run_id = run_id

    async def execute(
        self,
        tool_call: ToolCall,
        emit: Any,
    ) -> dict[str, Any]:
        """Execute a tool call, emit events, and return the result."""
        # Emit tool.called event
        await emit(
            RunEvent.create(
                self._run_id,
                EventType.TOOL_CALLED,
                {
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.name,
                    "arguments": tool_call.arguments,
                },
            )
        )

        try:
            result = await self._orchestrator.call_tool(tool_call.name, tool_call.arguments)

            # Emit tool.completed or tool.failed
            if result.is_error:
                await emit(
                    RunEvent.create(
                        self._run_id,
                        EventType.TOOL_FAILED,
                        {
                            "tool_call_id": tool_call.id,
                            "tool_name": tool_call.name,
                            "error": result.content,
                        },
                    )
                )
            else:
                await emit(
                    RunEvent.create(
                        self._run_id,
                        EventType.TOOL_COMPLETED,
                        {
                            "tool_call_id": tool_call.id,
                            "tool_name": tool_call.name,
                            "result": result.content,
                        },
                    )
                )

            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "content": result.content,
                "is_error": result.is_error,
                "artifacts": [
                    {"content_type": a.content_type, "name": a.name}
                    for a in (result.artifacts or [])
                ],
            }

        except Exception as e:
            await emit(
                RunEvent.create(
                    self._run_id,
                    EventType.TOOL_FAILED,
                    {
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.name,
                        "error": str(e),
                    },
                )
            )
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "content": f"Error: {e}",
                "is_error": True,
            }
