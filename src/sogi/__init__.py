"""Sogi: engineering discipline and focused context for coding agents."""

from .core.phases import EngineeringPhase
from .core.run_record import RunRecord, Telemetry
from .core.task_spec import TaskSpec
from .events.event import Event
from .runs.service import RunNotFoundError, RunService

__all__ = [
    "EngineeringPhase",
    "Event",
    "RunNotFoundError",
    "RunRecord",
    "RunService",
    "TaskSpec",
    "Telemetry",
]
__version__ = "0.1.0"
