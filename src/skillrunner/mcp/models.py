from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MCPTool:
    name: str
    description: str


@dataclass(slots=True)
class MCPToolResult:
    tool_name: str
    content: Any
    ok: bool = True
