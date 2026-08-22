"""Findings produced by deterministic governor checks.

A finding is a pure observation over the run's event stream: it carries no LLM
reasoning and no mutable state. The engine converts new findings into
``warning_raised`` events; deduplication is handled by the RunService via
``signature`` so a recurring situation does not spam identical interventions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One deterministic supervision observation."""

    kind: str
    subject: str
    message: str

    @property
    def signature(self) -> str:
        return f"{self.kind}:{self.subject}"
