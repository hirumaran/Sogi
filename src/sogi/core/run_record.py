"""RunRecord: the object representing one Sogi-supervised engineering session.

A RunRecord binds the deterministic task specification, the mutable engineering
state, the compiled repository context, and operational telemetry into a single
persistable unit. Every future subsystem (governor, verifier, MCP) operates on
a ``run_id`` and reads or mutates the record through :class:`RunService`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sogi.context.compiler import CompiledContext
from sogi.core.task_spec import TaskSpec
from sogi.state.engineering_state import EngineeringState

#: Verification statuses are deliberately non-binary so evidence can be
#: reported as defensibly unknown rather than forced into pass/fail.
SATISFIED = "SATISFIED"
VIOLATED = "VIOLATED"
UNVERIFIED = "UNVERIFIED"
VERIFICATION_STATUSES = frozenset({SATISFIED, VIOLATED, UNVERIFIED})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CommandRecord:
    """One command execution observed during a run."""

    command: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    result: str | None = None
    success: bool | None = None


@dataclass(frozen=True)
class WarningRecord:
    """A Sogi intervention (governor check, failed context compile, ...).

    ``subject`` identifies what the warning is about (a path, a command, ...)
    so repeated deterministic checks can deduplicate: one intervention per
    kind+subject unless the situation recurs after being addressed. Severity
    follows the governor's scale: INFO / WARNING / HIGH / CRITICAL.
    """

    kind: str
    message: str
    timestamp: str = field(default_factory=_now)
    subject: str | None = None
    severity: str = "WARNING"


@dataclass(frozen=True)
class VerificationRecord:
    """Evidence mapped back to one acceptance criterion."""

    criterion: str
    status: str
    evidence: tuple[str, ...] = ()
    timestamp: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in VERIFICATION_STATUSES:
            raise ValueError(f"Invalid verification status: {self.status!r}")


@dataclass(frozen=True)
class VerificationSnapshot:
    """A watermark pinning evidence to repository and stream state.

    Verification is only valid while nothing observable changed afterwards:
    any later ``file_modified`` event advances the stream past
    ``event_sequence``, and any worktree mutation changes the fingerprint.
    Either drift marks the verification STALE for completion-gating purposes.
    """

    event_sequence: int
    verified_at: str = field(default_factory=_now)
    git_head: str | None = None
    diff_hash: str | None = None
    outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_sequence": self.event_sequence,
            "verified_at": self.verified_at,
            "git_head": self.git_head,
            "diff_hash": self.diff_hash,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> VerificationSnapshot:
        return cls(
            event_sequence=int(payload.get("event_sequence", 0)),
            verified_at=str(payload.get("verified_at", _now())),
            git_head=payload.get("git_head"),
            diff_hash=payload.get("diff_hash"),
            outcome=str(payload.get("outcome", "")),
        )


@dataclass
class Telemetry:
    """Operational observations that are not part of engineering state."""

    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    commands: list[CommandRecord] = field(default_factory=list)
    warnings: list[WarningRecord] = field(default_factory=list)
    verification: list[VerificationRecord] = field(default_factory=list)
    context_compilations: int = 0
    context_budget: int = 4000
    context_tokens: list[int] = field(default_factory=list)
    #: Outcome of the most recent independent verification pass
    #: (PASS / PASS_WITH_UNVERIFIED / FAIL / INCONCLUSIVE).
    last_verification_outcome: str | None = None
    #: Watermark of the most recent verification pass for staleness gating.
    verification_snapshot: VerificationSnapshot | None = None
    #: Deterministic working-tree assessment captured by assess_patch().
    patch_assessment: dict[str, Any] | None = None
    #: Host/model-reported usage. These are measurements with provenance:
    #: values are only present when a host or model API actually reported
    #: them; Sogi never estimates silently.
    agent_host: str | None = None
    agent_version: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    #: Final run outcome once completion is gated through (or forced past) the
    #: verifier: completed / completed_with_unverified / completion_forced.
    outcome: str | None = None
    started_at: str = field(default_factory=_now)
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_read": list(self.files_read),
            "files_modified": list(self.files_modified),
            "commands": [asdict(item) for item in self.commands],
            "warnings": [asdict(item) for item in self.warnings],
            "verification": [asdict(item) for item in self.verification],
            "context_compilations": self.context_compilations,
            "context_budget": self.context_budget,
            "context_tokens": list(self.context_tokens),
            "last_verification_outcome": self.last_verification_outcome,
            "verification_snapshot": (
                self.verification_snapshot.to_dict() if self.verification_snapshot else None
            ),
            "patch_assessment": self.patch_assessment,
            "agent_host": self.agent_host,
            "agent_version": self.agent_version,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": self.cost_usd,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Telemetry:
        return cls(
            files_read=list(payload.get("files_read", [])),
            files_modified=list(payload.get("files_modified", [])),
            commands=[CommandRecord(**item) for item in payload.get("commands", [])],
            warnings=[WarningRecord(**item) for item in payload.get("warnings", [])],
            verification=[
                VerificationRecord(**{**item, "evidence": tuple(item.get("evidence", ()))})
                for item in payload.get("verification", [])
            ],
            context_compilations=int(payload.get("context_compilations", 0)),
            context_budget=int(payload.get("context_budget", 4000)),
            context_tokens=list(payload.get("context_tokens", [])),
            last_verification_outcome=payload.get("last_verification_outcome"),
            verification_snapshot=(
                VerificationSnapshot.from_dict(payload["verification_snapshot"])
                if payload.get("verification_snapshot")
                else None
            ),
            patch_assessment=payload.get("patch_assessment"),
            agent_host=payload.get("agent_host"),
            agent_version=payload.get("agent_version"),
            model=payload.get("model"),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            cached_tokens=int(payload.get("cached_tokens", 0)),
            cost_usd=payload.get("cost_usd"),
            outcome=payload.get("outcome"),
            started_at=str(payload.get("started_at", _now())),
            completed_at=payload.get("completed_at"),
        )


@dataclass
class RunRecord:
    """One complete Sogi-supervised engineering session."""

    run_id: str
    task: TaskSpec
    state: EngineeringState
    context: CompiledContext | None = None
    telemetry: Telemetry = field(default_factory=Telemetry)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task.to_dict(),
            "state": self.state.to_dict(),
            "context": self.context.to_dict() if self.context else None,
            "telemetry": self.telemetry.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunRecord:
        context = payload.get("context")
        return cls(
            run_id=str(payload["run_id"]),
            task=TaskSpec.from_dict(payload["task"]),
            state=EngineeringState.from_dict(payload["state"]),
            context=CompiledContext.from_dict(context) if context else None,
            telemetry=Telemetry.from_dict(payload.get("telemetry", {})),
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
        )
