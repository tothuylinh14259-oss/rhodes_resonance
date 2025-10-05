from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from itertools import count
from typing import Any, Dict, List, Optional, Tuple


class EventType(str, Enum):
    """Supported event categories for the demo logging pipeline."""

    TURN_START = "turn_start"
    TURN_END = "turn_end"
    ACTION = "action"
    STATE_UPDATE = "state_update"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    NARRATIVE = "narrative"
    SYSTEM = "system"


def _clean_value(value: Any) -> Any:
    """Recursively remove ``None`` values from dictionaries/lists."""

    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean_value(v) for v in value if v is not None]
    if isinstance(value, tuple):
        cleaned = [_clean_value(v) for v in value if v is not None]
        return cleaned
    return value


@dataclass
class Event:
    """Structured event emitted by the runtime.

    All events are normalised via :class:`EventBus` before being delivered to subscribers.
    """

    event_type: EventType
    turn: Optional[int] = None
    phase: Optional[str] = None
    actor: Optional[str] = None
    step: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[datetime] = None
    sequence: Optional[int] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, EventType):
            try:
                self.event_type = EventType(str(self.event_type))
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(f"Unsupported event type: {self.event_type}") from exc
        if self.data is None:
            self.data = {}
        if not isinstance(self.data, dict):
            raise TypeError("Event.data must be a dict")
        self.data = _clean_value(self.data)  # drop ``None`` children

    @property
    def event_id(self) -> str:
        seq = self.sequence or 0
        return f"EVT-{seq:06d}"

    def assign_runtime_fields(self, sequence: int, timestamp: datetime) -> None:
        self.sequence = sequence
        self.timestamp = timestamp

    def validate(self) -> None:
        required_keys: Dict[EventType, List[str]] = {
            EventType.ACTION: ["action"],
            EventType.STATE_UPDATE: ["state"],
            EventType.TOOL_CALL: ["tool"],
            EventType.TOOL_RESULT: ["tool"],
            EventType.ERROR: ["message"],
            EventType.NARRATIVE: ["text"],
        }
        expected = required_keys.get(self.event_type)
        if not expected:
            return
        missing = [key for key in expected if key not in self.data]
        if missing:
            raise ValueError(
                f"Event '{self.event_type.value}' missing required fields: {', '.join(missing)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        if self.timestamp is None or self.sequence is None:
            raise RuntimeError("Event must be normalised by EventBus before serialisation")
        payload: Dict[str, Any] = {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
        }
        if self.turn is not None:
            payload["turn"] = self.turn
        if self.phase is not None:
            payload["phase"] = self.phase
        if self.actor is not None:
            payload["actor"] = self.actor
        if self.step is not None:
            payload["step"] = self.step
        if self.correlation_id is not None:
            payload["correlation_id"] = self.correlation_id
        for key, value in self.data.items():
            payload[key] = value
        return payload


class SequenceGenerator:
    """Thread-unsafe monotonically increasing sequence counter."""

    def __init__(self) -> None:
        self._counter = count(1)

    def next(self) -> int:
        return next(self._counter)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
