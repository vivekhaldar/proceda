from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


@dataclass(slots=True)
class RunEvent:
    run_id: str
    type: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["timestamp"] = self.timestamp.isoformat()
        return out


class EventSink(Protocol):
    async def handle(self, event: RunEvent) -> None: ...


class CompositeEventSink:
    def __init__(self, sinks: list[EventSink] | None = None) -> None:
        self._sinks = sinks or []

    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    async def handle(self, event: RunEvent) -> None:
        for sink in self._sinks:
            await sink.handle(event)
