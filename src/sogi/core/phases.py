from __future__ import annotations

from enum import Enum


class EngineeringPhase(str, Enum):
    UNDERSTAND = "understand"
    INVESTIGATE = "investigate"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"
    DONE = "done"


_NEXT_PHASE: dict[EngineeringPhase, EngineeringPhase] = {
    EngineeringPhase.UNDERSTAND: EngineeringPhase.INVESTIGATE,
    EngineeringPhase.INVESTIGATE: EngineeringPhase.PLAN,
    EngineeringPhase.PLAN: EngineeringPhase.IMPLEMENT,
    EngineeringPhase.IMPLEMENT: EngineeringPhase.VERIFY,
    EngineeringPhase.VERIFY: EngineeringPhase.REVIEW,
    EngineeringPhase.REVIEW: EngineeringPhase.DONE,
}


def next_phase(current: EngineeringPhase) -> EngineeringPhase | None:
    return _NEXT_PHASE.get(current)


def can_transition(current: EngineeringPhase, target: EngineeringPhase) -> bool:
    """Only allow the explicit forward lifecycle in the MVP."""
    return next_phase(current) == target
