"""Append-only event model.

Every observable thing that happens during a run becomes an event. The event log
is the source of truth; RunRecord snapshots are derived state. This keeps the
system replayable and gives the governor a single stream to inspect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

EVENT_TYPES = frozenset(
    {
        "task_created",
        "context_compiled",
        "file_read",
        "file_modified",
        "command_started",
        "command_finished",
        "decision_recorded",
        "phase_changed",
        "warning_raised",
        "verification_started",
        "verification_result",
        "usage_recorded",
        "run_completed",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    """One immutable entry in a run's append-only event log."""

    type: str
    run_id: str
    timestamp: str = field(default_factory=_now)
    sequence: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {self.type!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Event:
        return cls(
            type=str(payload["type"]),
            run_id=str(payload["run_id"]),
            timestamp=str(payload.get("timestamp", _now())),
            sequence=int(payload.get("sequence", 0)),
            payload=dict(payload.get("payload", {})),
        )
