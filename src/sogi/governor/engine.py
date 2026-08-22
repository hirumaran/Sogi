"""The Engineering Governor: deterministic supervision over a run.

The governor inspects the append-only event stream — the source of truth — and
emits findings for bad engineering patterns: repeated exploration, failure
loops, and scope expansion. It is deliberately pure and LLM-free; every check
is a deterministic function of the run record and its events, so results are
reproducible and replayable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sogi.core.phases import EngineeringPhase
from sogi.core.run_record import RunRecord
from sogi.events.event import Event

from .failures import check_failure_loops
from .finding import Finding
from .reads import check_repeated_reads
from .scope import check_scope_expansion

__all__ = ["Governor", "Finding"]


@dataclass
class Governor:
    """Runs all deterministic checks over one run's event history."""

    read_threshold: int = 3
    failure_threshold: int = 3

    def inspect(self, record: RunRecord, events: list[Event]) -> tuple[Finding, ...]:
        """Return every finding currently visible in the event stream."""
        if record.state.phase is EngineeringPhase.DONE:
            return ()
        return (
            *check_repeated_reads(events, threshold=self.read_threshold),
            *check_failure_loops(events, threshold=self.failure_threshold),
            *check_scope_expansion(record, events),
        )
