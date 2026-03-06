from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ApprovalRequest:
    step_index: int
    step_title: str
    approval_type: Literal["pre_step", "post_step"]
    context: str
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ClarificationRequest:
    question: str
    options: list[str]
    context: str | None = None


@dataclass(slots=True)
class RunMessage:
    id: str
    role: Literal["system", "assistant", "user", "tool"]
    content: str
    timestamp: datetime
    tool_call_id: str | None = None
    app_name: str | None = None
    tool_calls: list[ToolCall] | None = None


@dataclass(slots=True)
class ApprovalRecord:
    step_index: int
    approval_type: Literal["pre_step", "post_step"]
    approved: bool
    timestamp: datetime


@dataclass(slots=True)
class RunSession:
    id: str
    skill_id: str
    skill_name: str
    status: RunStatus = RunStatus.CREATED
    current_step: int = 0
    messages: list[RunMessage] = field(default_factory=list)
    pending_approval: ApprovalRequest | None = None
    pending_clarification: ClarificationRequest | None = None
    pending_error: dict[str, Any] | None = None
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    step_tool_results: list[dict[str, Any]] = field(default_factory=list)
    approval_records: list[ApprovalRecord] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, *, skill_id: str, skill_name: str) -> "RunSession":
        return cls(id=f"run_{uuid4().hex[:12]}", skill_id=skill_id, skill_name=skill_name)


@dataclass(slots=True)
class RunResult:
    session_id: str
    status: RunStatus
    summary: str
    completed_steps: int
    failed_step: int | None
    event_log_path: Path
