"""ABOUTME: Structured event model for runtime transitions and state changes.
ABOUTME: Defines RunEvent, EventType, EventSink protocol, and sink implementations.
"""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


class EventType(enum.Enum):
    """All event types emitted by the runtime."""

    # Lifecycle
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # Steps
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_SKIPPED = "step.skipped"

    # Messages
    MESSAGE_SYSTEM = "message.system"
    MESSAGE_ASSISTANT = "message.assistant"
    MESSAGE_USER = "message.user"
    MESSAGE_TOOL = "message.tool"
    MESSAGE_REASONING = "message.reasoning"

    # Tools
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    # Human interaction
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESPONDED = "approval.responded"
    CLARIFICATION_REQUESTED = "clarification.requested"
    CLARIFICATION_RESPONDED = "clarification.responded"
    ERROR_RECOVERY_REQUESTED = "error.recovery_requested"
    ERROR_RECOVERY_SELECTED = "error.recovery_selected"

    # LLM usage
    LLM_USAGE = "llm.usage"

    # Cache
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    CACHE_FALLBACK = "cache.fallback"

    # Runtime state
    STATUS_CHANGED = "status.changed"
    CONTEXT_UPDATED = "context.updated"
    SUMMARY_GENERATED = "summary.generated"


@dataclass
class RunEvent:
    """A single runtime event."""

    id: str
    timestamp: datetime
    run_id: str
    type: EventType
    payload: dict[str, Any]

    @staticmethod
    def create(
        run_id: str,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        return RunEvent(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(UTC),
            run_id=run_id,
            type=event_type,
            payload=payload or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "run_id": self.run_id,
            "type": self.type.value,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RunEvent:
        return RunEvent(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            run_id=data["run_id"],
            type=EventType(data["type"]),
            payload=data.get("payload", {}),
        )

    @staticmethod
    def from_json(line: str) -> RunEvent:
        return RunEvent.from_dict(json.loads(line))


class EventSink(Protocol):
    """Protocol for consuming runtime events."""

    async def handle(self, event: RunEvent) -> None: ...


class CompositeEventSink:
    """Fans out events to multiple sinks."""

    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        self._sinks: list[EventSink] = sinks or []

    def add(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    async def handle(self, event: RunEvent) -> None:
        for sink in self._sinks:
            await sink.handle(event)


class NullEventSink:
    """Discards all events. Useful for tests."""

    async def handle(self, event: RunEvent) -> None:
        pass


class CollectorEventSink:
    """Collects events in memory. Useful for tests."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def handle(self, event: RunEvent) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[RunEvent]:
        return [e for e in self.events if e.type == event_type]
