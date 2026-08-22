"""Findings produced by deterministic governor checks.

A finding is a pure observation over the run's event stream: it carries no LLM
reasoning and no mutable state. The engine converts new findings into
``warning_raised`` events; deduplication is handled by the RunService via
``signature`` so a recurring situation does not spam identical interventions.
"""

from __future__ import annotations

from dataclasses import dataclass

INFO = "INFO"
WARNING = "WARNING"
HIGH = "HIGH"
CRITICAL = "CRITICAL"
SEVERITIES = (INFO, WARNING, HIGH, CRITICAL)

#: Default completion-policy weight per finding kind. Scope expansion is HIGH:
#: unrelated modifications block completion until explicitly acknowledged.
KIND_SEVERITY: dict[str, str] = {
    "repeated_read": WARNING,
    "failure_loop": WARNING,
    "scope_expansion": HIGH,
    "test_tampering": CRITICAL,
    "dependency_change": HIGH,
    "completion_forced": CRITICAL,
}


@dataclass(frozen=True)
class Finding:
    """One deterministic supervision observation."""

    kind: str
    subject: str
    message: str
    severity: str = WARNING

    @property
    def signature(self) -> str:
        return f"{self.kind}:{self.subject}"
