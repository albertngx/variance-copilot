"""Run events for the audit trail (SPEC §15.1); the hash-chained writer arrives in M5-01."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

EventType = Literal[
    "RUN_STARTED",
    "LLM_CALL",
    "TOOL_CALL",
    "FACTS_REGISTERED",
    "VALIDATION",
    "REPAIR_REQUESTED",
    "RUN_COMPLETED",
    "RUN_FAILED",
]


@dataclass(frozen=True)
class RunEvent:
    event_type: EventType
    payload: Mapping[str, Any]


class RunEvents(Protocol):
    def emit(self, event_type: EventType, payload: Mapping[str, Any]) -> None: ...


@dataclass
class MemoryEvents:
    """Keeps a run's events in order; stands in for the audit writer until M5-01."""

    events: list[RunEvent] = field(default_factory=list)

    def emit(self, event_type: EventType, payload: Mapping[str, Any]) -> None:
        self.events.append(RunEvent(event_type, dict(payload)))

    @property
    def types(self) -> list[EventType]:
        return [event.event_type for event in self.events]
