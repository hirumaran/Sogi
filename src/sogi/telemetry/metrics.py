"""Run metrics: did Sogi actually help?

Deterministic counters derived from a RunRecord — no estimates, no claims.
These numbers are the basis for any future controlled comparison of
"agent alone" versus "agent + Sogi"; nothing here asserts improvement
without such an experiment.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sogi.core.run_record import RunRecord


def _parse(timestamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None


@dataclass
class RunMetrics:
    """Observable efficiency and discipline metrics for one run."""

    run_id: str
    phase: str
    files_read: int = 0
    unique_files_read: int = 0
    repeat_reads: int = 0
    files_modified: int = 0
    commands_executed: int = 0
    failed_commands: int = 0
    warnings: Counter[str] = field(default_factory=Counter)
    interventions: int = 0
    context_compilations: int = 0
    context_budget: int = 0
    last_context_tokens: int | None = None
    verification_satisfied: int = 0
    verification_violated: int = 0
    verification_unverified: int = 0
    duration_seconds: float | None = None

    @classmethod
    def from_record(cls, record: RunRecord) -> RunMetrics:
        telemetry = record.telemetry
        read_counts = Counter(telemetry.files_read)
        failed = sum(1 for command in telemetry.commands if command.success is False)
        finished = sum(1 for command in telemetry.commands if command.finished_at)
        warning_kinds = Counter(warning.kind for warning in telemetry.warnings)

        started = _parse(telemetry.started_at)
        ended = _parse(telemetry.completed_at) if telemetry.completed_at else None
        duration = round((ended - started).total_seconds(), 3) if started and ended else None

        statuses = Counter(item.status for item in telemetry.verification)
        context_tokens = telemetry.context_tokens

        return cls(
            run_id=record.run_id,
            phase=record.state.phase.value,
            files_read=len(telemetry.files_read),
            unique_files_read=len(read_counts),
            repeat_reads=sum(count - 1 for count in read_counts.values()),
            files_modified=len(telemetry.files_modified),
            commands_executed=finished,
            failed_commands=failed,
            warnings=warning_kinds,
            interventions=sum(warning_kinds.values()),
            context_compilations=telemetry.context_compilations,
            context_budget=telemetry.context_budget,
            last_context_tokens=context_tokens[-1] if context_tokens else None,
            verification_satisfied=statuses.get("SATISFIED", 0),
            verification_violated=statuses.get("VIOLATED", 0),
            verification_unverified=statuses.get("UNVERIFIED", 0),
            duration_seconds=duration,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "phase": self.phase,
            "exploration": {
                "files_read": self.files_read,
                "unique_files_read": self.unique_files_read,
                "repeat_reads": self.repeat_reads,
            },
            "changes": {
                "files_modified": self.files_modified,
                "commands_executed": self.commands_executed,
                "failed_commands": self.failed_commands,
            },
            "supervision": {
                "interventions": self.interventions,
                "warnings_by_kind": dict(self.warnings),
            },
            "context": {
                "compilations": self.context_compilations,
                "budget": self.context_budget,
                "last_selected_tokens": self.last_context_tokens,
            },
            "verification": {
                "satisfied": self.verification_satisfied,
                "violated": self.verification_violated,
                "unverified": self.verification_unverified,
            },
            "duration_seconds": self.duration_seconds,
        }

    def render(self) -> str:
        data = self.to_dict()
        lines = [f"METRICS run {self.run_id}  phase={self.phase.upper()}", ""]
        exploration = data["exploration"]
        lines.append(
            f"  Files read: {exploration['files_read']} "
            f"(unique {exploration['unique_files_read']}, "
            f"repeat {exploration['repeat_reads']})"
        )
        changes = data["changes"]
        lines.append(
            f"  Files modified: {changes['files_modified']}  "
            f"Commands: {changes['commands_executed']} "
            f"(failed {changes['failed_commands']})"
        )
        supervision = data["supervision"]
        lines.append(f"  Sogi interventions: {supervision['interventions']}")
        for kind, count in sorted(supervision["warnings_by_kind"].items()):
            lines.append(f"    {kind}: {count}")
        context = data["context"]
        lines.append(
            f"  Context: {context['compilations']} compilations, "
            f"last {context['last_selected_tokens']}/{context['budget']} tokens"
        )
        verification = data["verification"]
        lines.append(
            f"  Verification: {verification['satisfied']} satisfied, "
            f"{verification['violated']} violated, "
            f"{verification['unverified']} unverified"
        )
        if self.duration_seconds is not None:
            lines.append(f"  Duration: {self.duration_seconds}s")
        return "\n".join(lines)
