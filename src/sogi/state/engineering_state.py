from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sogi.core.phases import EngineeringPhase, can_transition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EngineeringState:
    task_id: str
    objective: str
    phase: EngineeringPhase = EngineeringPhase.UNDERSTAND
    constraints: list[str] = field(default_factory=list)
    files_examined: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    verification: dict[str, bool | None] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    #: Governor findings acknowledged by a human or agent policy decision,
    #: keyed by "kind:subject" with the acknowledgement timestamp. Required
    #: before completion when the finding is HIGH/CRITICAL severity.
    acknowledged: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def transition_to(self, target: EngineeringPhase) -> None:
        if not can_transition(self.phase, target):
            raise ValueError(f"Invalid engineering phase transition: {self.phase} -> {target}")
        self.phase = target
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EngineeringState:
        values = dict(payload)
        values["phase"] = EngineeringPhase(values["phase"])
        return cls(**values)
