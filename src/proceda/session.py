"""ABOUTME: Session data models: mutable run state and related types.
ABOUTME: Defines RunMessage, RunSession, approval/error types, and run lifecycle."""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


class RunStatus(enum.Enum):
    """Lifecycle states for a run session."""

    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (
            RunStatus.RUNNING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.AWAITING_INPUT,
        )


@dataclass
class ToolCall:
    """A tool call made by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]

    @staticmethod
    def generate_id() -> str:
        return f"tc_{uuid.uuid4().hex[:12]}"


@dataclass
class RunMessage:
    """A message in the run conversation."""

    id: str
    role: Literal["system", "assistant", "user", "tool"]
    content: str
    timestamp: datetime
    tool_call_id: str | None = None
    app_name: str | None = None
    tool_calls: list[ToolCall] | None = None
    is_critical: bool = False

    @staticmethod
    def create(
        role: Literal["system", "assistant", "user", "tool"],
        content: str,
        tool_call_id: str | None = None,
        app_name: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        is_critical: bool = False,
    ) -> RunMessage:
        return RunMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            role=role,
            content=content,
            timestamp=datetime.now(UTC),
            tool_call_id=tool_call_id,
            app_name=app_name,
            tool_calls=tool_calls,
            is_critical=is_critical,
        )


@dataclass
class ApprovalRequest:
    """A request for human approval."""

    step_index: int
    step_title: str
    approval_type: Literal["pre_step", "post_step"]
    context: str
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ClarificationRequest:
    """A request for human clarification."""

    question: str
    options: list[str] = field(default_factory=list)
    context: str | None = None


@dataclass(frozen=True)
class ExecutedToolReceipt:
    """Immutable execution evidence, independent of reusable provider call IDs.

    Arguments are stored as JSON so nested caller-owned objects cannot mutate history.
    The result is the exact tool response text, not an assistant summary.
    """

    receipt_id: str
    tool_call_id: str
    step_index: int
    requested_tool_name: str
    qualified_tool_name: str | None
    sop_tool_name: str | None
    arguments_json: str
    result: str
    is_error: bool | None

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self.arguments_json)


@dataclass(frozen=True)
class ClarificationRecord:
    """An actual human-interface answer, retained separately from model messages."""

    receipt_id: str
    tool_call_id: str
    step_index: int
    question: str
    options: tuple[str, ...]
    answer: str


@dataclass
class ErrorContext:
    """Context about a pending error for recovery decisions."""

    error_type: str
    message: str
    step_index: int | None = None
    tool_name: str | None = None


class ApprovalDecision(enum.Enum):
    """User decisions on approval requests."""

    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"


@dataclass
class ErrorRecoveryRequest:
    """A request for the user to decide how to handle an error."""

    error: ErrorContext
    options: list[str] = field(default_factory=lambda: ["retry", "skip", "cancel"])


class ErrorRecoveryDecision(enum.Enum):
    """User decisions on error recovery."""

    RETRY = "retry"
    SKIP = "skip"
    CANCEL = "cancel"


@dataclass
class ApprovalRecord:
    """A recorded approval decision."""

    step_index: int
    approval_type: Literal["pre_step", "post_step"]
    decision: ApprovalDecision
    timestamp: datetime
    comment: str | None = None


@dataclass
class RunSession:
    """Mutable state for a single skill execution run."""

    id: str
    skill_id: str
    skill_name: str
    status: RunStatus
    current_step: int
    messages: list[RunMessage] = field(default_factory=list)
    pending_approval: ApprovalRequest | None = None
    pending_clarification: ClarificationRequest | None = None
    pending_error: ErrorContext | None = None
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    step_tool_results: list[dict[str, Any]] = field(default_factory=list)
    executed_tool_receipts: list[ExecutedToolReceipt] = field(default_factory=list)
    clarification_records: list[ClarificationRecord] = field(default_factory=list)
    approval_records: list[ApprovalRecord] = field(default_factory=list)
    completed_steps: list[int] = field(default_factory=list)
    skipped_steps: list[int] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_llm_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    variables: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def create(
        skill_id: str, skill_name: str, variables: dict[str, str] | None = None
    ) -> RunSession:
        return RunSession(
            id=f"run_{uuid.uuid4().hex[:12]}",
            skill_id=skill_id,
            skill_name=skill_name,
            status=RunStatus.CREATED,
            current_step=1,
            variables=variables or {},
        )

    def touch(self) -> None:
        self.last_activity_at = datetime.now(UTC)

    def set_status(self, status: RunStatus) -> None:
        self.status = status
        self.touch()
        if status == RunStatus.RUNNING and self.started_at is None:
            self.started_at = datetime.now(UTC)
        if status.is_terminal:
            self.completed_at = datetime.now(UTC)

    def add_message(self, message: RunMessage) -> None:
        self.messages.append(message)
        self.touch()

    def complete_current_step(self) -> None:
        self.completed_steps.append(self.current_step)
        self.touch()

    def advance_step(self) -> None:
        self.current_step += 1
        self.step_tool_results.clear()
        self.pending_tool_calls.clear()
        self.touch()


@dataclass(frozen=True)
class RunResult:
    """Final result of a completed run."""

    session_id: str
    status: RunStatus
    summary: str
    completed_steps: int
    total_steps: int
    failed_step: int | None = None
    event_log_path: Path | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
