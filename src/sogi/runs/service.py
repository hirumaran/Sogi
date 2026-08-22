"""RunService: lifecycle orchestration for Sogi runs.

Every mutation follows the same shape: load the run, mutate the record, append
an event to the append-only log, then persist the derived snapshot. The event
log is the source of truth; the RunRecord snapshot is a materialized projection
of it for fast reads. Events carry enough payload to reconstruct the record,
which is what future replay will use.
"""

from __future__ import annotations

import contextlib
import fcntl
import secrets
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from sogi.context.compiler import CompiledContext, ContextCompiler
from sogi.core.phases import EngineeringPhase
from sogi.core.run_record import (
    CommandRecord,
    RunRecord,
    Telemetry,
    VerificationRecord,
    VerificationSnapshot,
    WarningRecord,
)
from sogi.core.task_spec import TaskSpec
from sogi.events.event import Event
from sogi.events.log import EventLog
from sogi.governor import Governor
from sogi.repository.provider import RepositoryProvider
from sogi.repository.tree_sitter_provider import AnalyzerCommandError, TreeSitterProvider
from sogi.repository.worktree import capture_fingerprint
from sogi.state.engineering_state import EngineeringState
from sogi.storage.db import SogiDatabase
from sogi.verification.discovery import DiscoveredCheck
from sogi.verification.verifier import VerificationReport, Verifier

from .render import render_run_start, render_run_state


class RunNotFoundError(KeyError):
    """Raised when a run_id does not exist in the store."""


class CompletionGateError(RuntimeError):
    """Raised when completion is attempted without sufficient evidence.

    Sogi's core promise is that an unsupported "done" claim is rejected.
    Every rejection carries the reason so a coding agent can act on it
    (run verification, fix failures, or acknowledge unverified criteria).
    """

    def __init__(self, reason: str, *, remediation: str) -> None:
        super().__init__(f"{reason} {remediation}")
        self.reason = reason
        self.remediation = remediation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunService:
    """Coordinates run creation, mutation, and persistence."""

    def __init__(
        self,
        repo_root: Path,
        *,
        analyzer_command: tuple[str, ...] | None = None,
        provider: RepositoryProvider | None = None,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        if not self.repo_root.is_dir():
            raise ValueError(f"Repository does not exist: {self.repo_root}")
        self.sogi_dir = self.repo_root / ".sogi"
        self.db = SogiDatabase(self.sogi_dir)
        self.events = EventLog(self.db)
        self._provider = provider
        self._analyzer_command = analyzer_command
        self._lock = threading.Lock()
        self.governor = Governor()

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        objective: str,
        *,
        acceptance_criteria: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
        budget: int = 4000,
        compile_context: bool = True,
    ) -> RunRecord:
        """Create a run, understand the task, and (best-effort) compile context."""
        run_id = self._new_run_id()
        task = TaskSpec.from_prompt(
            objective,
            acceptance_criteria=tuple(item.strip() for item in acceptance_criteria if item.strip()),
            constraints=tuple(item.strip() for item in constraints if item.strip()),
        )
        state = EngineeringState(
            task_id=run_id,
            objective=task.objective,
            constraints=list(task.constraints),
        )
        telemetry = Telemetry(context_budget=budget)
        record = RunRecord(run_id=run_id, task=task, state=state, telemetry=telemetry)
        with self._file_lock():
            self.db.save_run_with_events(
                record,
                [
                    Event(
                        type="task_created",
                        run_id=run_id,
                        payload={
                            "objective": task.objective,
                            "acceptance_criteria": list(task.acceptance_criteria),
                            "constraints": list(task.constraints),
                            "budget": budget,
                        },
                    )
                ],
            )
            self._write_active_run(run_id)
        if compile_context:
            try:
                self.compile_context(run_id, budget=budget)
            except (AnalyzerCommandError, OSError, ValueError) as exc:
                self.raise_warning(run_id, "context_compile_failed", str(exc))
        return self.get(run_id)

    def compile_context(
        self,
        run_id: str,
        *,
        budget: int | None = None,
        prepare: bool = True,
    ) -> CompiledContext:
        """Compile (or refresh) the run's repository context under its budget."""

        def mutate(record: RunRecord, now: str) -> list[Event]:
            token_budget = budget or record.telemetry.context_budget
            compiled = ContextCompiler(self._provider_for(), token_budget=token_budget).compile(
                record.task, prepare=prepare
            )
            record.context = compiled
            record.telemetry.context_budget = token_budget
            record.telemetry.context_compilations += 1
            record.telemetry.context_tokens.append(compiled.selected_tokens)
            if record.state.phase == EngineeringPhase.UNDERSTAND:
                record.state.phase = EngineeringPhase.INVESTIGATE
                return [
                    Event(
                        type="context_compiled",
                        run_id=run_id,
                        timestamp=now,
                        payload={
                            "selected_tokens": compiled.selected_tokens,
                            "budget": token_budget,
                            "files": list(compiled.related_files),
                        },
                    ),
                    Event(
                        type="phase_changed",
                        run_id=run_id,
                        timestamp=now,
                        payload={
                            "from": EngineeringPhase.UNDERSTAND.value,
                            "to": EngineeringPhase.INVESTIGATE.value,
                        },
                    ),
                ]
            return [
                Event(
                    type="context_compiled",
                    run_id=run_id,
                    timestamp=now,
                    payload={
                        "selected_tokens": compiled.selected_tokens,
                        "budget": token_budget,
                        "files": list(compiled.related_files),
                    },
                )
            ]

        self._mutate(run_id, mutate)
        return self.get(run_id).context  # type: ignore[return-value]

    def complete(
        self,
        run_id: str,
        *,
        allow_unverified: bool = False,
        force: bool = False,
    ) -> RunRecord:
        """Gate completion through independent verification evidence.

        Rules:
        - a verification pass must have run (otherwise the agent's "done"
          claim is unsupported and rejected);
        - FAIL or INCONCLUSIVE outcomes block completion;
        - PASS_WITH_UNVERIFIED requires an explicit ``allow_unverified``
          policy decision;
        - ``force`` overrides any rejection but records a visible
          intervention, so bypasses are never silent.
        """

        def mutate(rec: RunRecord, now: str) -> list[Event]:
            events: list[Event] = []
            outcome = rec.telemetry.outcome

            if not force:
                self._check_gate(rec, allow_unverified)
                if rec.telemetry.last_verification_outcome == "PASS":
                    outcome = "completed"
                else:
                    outcome = "completed_with_unverified"
            else:
                outcome = "completion_forced"
                rec.telemetry.warnings.append(
                    WarningRecord(
                        kind="completion_forced",
                        message=(
                            "Completion forced without satisfying the verification "
                            f"gate (last outcome: {rec.telemetry.last_verification_outcome})."
                        ),
                        timestamp=now,
                        subject=run_id,
                    )
                )
                events.append(
                    Event(
                        type="warning_raised",
                        run_id=run_id,
                        timestamp=now,
                        payload={
                            "kind": "completion_forced",
                            "message": "Completion gate bypassed with force=True.",
                        },
                    )
                )

            if rec.state.phase != EngineeringPhase.DONE:
                events.append(
                    Event(
                        type="phase_changed",
                        run_id=run_id,
                        timestamp=now,
                        payload={"from": rec.state.phase.value, "to": EngineeringPhase.DONE.value},
                    )
                )
                rec.state.phase = EngineeringPhase.DONE
            rec.telemetry.outcome = outcome
            rec.telemetry.completed_at = now
            events.append(Event(type="run_completed", run_id=run_id, timestamp=now))
            return events

        with self._file_lock():
            fresh = self.get(run_id)
            now = _now()
            events = mutate(fresh, now)
            events.extend(self._govern(fresh, events, now))
            fresh.state.updated_at = now
            fresh.updated_at = now
            self.db.save_run_with_events(fresh, events)
            self._clear_active_run(run_id)
        return self.get(run_id)

    def acknowledge(self, run_id: str, kind: str, subject: str) -> None:
        """Record an explicit policy decision to accept a governor finding.

        HIGH and CRITICAL findings block completion until acknowledged here;
        the acknowledgement is itself an auditable event.
        """

        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.state.acknowledged[f"{kind}:{subject}"] = now
            return [
                Event(
                    type="decision_recorded",
                    run_id=run_id,
                    timestamp=now,
                    payload={
                        "kind": "acknowledge",
                        "warning_kind": kind,
                        "subject": subject,
                    },
                )
            ]

        self._mutate(run_id, mutate)

    def _check_gate(self, record: RunRecord, allow_unverified: bool) -> None:
        outcome = record.telemetry.last_verification_outcome
        if outcome is None:
            raise CompletionGateError(
                "No independent verification has run for this task.",
                remediation="Run `sogi verify <run_id>` (or the verify tool) before completing.",
            )
        self._check_stale(record)
        self._check_unresolved_findings(record)
        if outcome in {"FAIL", "INCONCLUSIVE"}:
            raise CompletionGateError(
                f"Verification outcome is {outcome}.",
                remediation="Fix the failing checks or violated criteria, then verify again.",
            )
        if outcome == "PASS_WITH_UNVERIFIED" and not allow_unverified:
            raise CompletionGateError(
                "Verification passed but some acceptance criteria remain unverified.",
                remediation=(
                    "Add evidence for those criteria and re-verify, or accept them "
                    "explicitly with allow_unverified=True."
                ),
            )

    def _check_unresolved_findings(self, record: RunRecord) -> None:
        """Block completion on unacknowledged HIGH/CRITICAL findings."""
        blocking = [
            warning
            for warning in record.telemetry.warnings
            if warning.severity in {"HIGH", "CRITICAL"}
            and f"{warning.kind}:{warning.subject}" not in record.state.acknowledged
        ]
        if not blocking:
            return
        listed = "; ".join(f"{w.kind}({w.subject})" for w in blocking[:3])
        raise CompletionGateError(
            f"Unresolved high-severity finding(s): {listed}.",
            remediation=(
                "Resolve the underlying issue, or accept it explicitly via "
                "`sogi acknowledge` / service.acknowledge()."
            ),
        )

    def _check_stale(self, record: RunRecord) -> None:
        """Reject evidence that predates later repository changes.

        Two independent drift signals:
        - the event stream advanced past the watermark via file_modified;
        - the worktree fingerprint changed since verification ran.
        """
        snapshot = record.telemetry.verification_snapshot
        if snapshot is None:
            return
        run_id = record.run_id

        last_modification = self.db.last_sequence_of_type(run_id, "file_modified")
        if last_modification is not None and last_modification > snapshot.event_sequence:
            raise CompletionGateError(
                "Verification is stale: files changed after the last verify.",
                remediation="Re-run `sogi verify <run_id>` to refresh the evidence.",
            )

        current = capture_fingerprint(self.repo_root)
        if (
            snapshot.diff_hash is not None
            and current.diff_hash is not None
            and current.diff_hash != snapshot.diff_hash
        ):
            raise CompletionGateError(
                "Verification is stale: the repository worktree changed after the last verify.",
                remediation="Re-run `sogi verify <run_id>` to refresh the evidence.",
            )

    def _clear_active_run(self, run_id: str) -> None:
        marker = self.sogi_dir / "active_run"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == run_id:
            marker.unlink(missing_ok=True)

    # -- observations --------------------------------------------------------

    def record_decision(self, run_id: str, decision: str) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.state.decisions.append(decision)
            return [
                Event(
                    type="decision_recorded",
                    run_id=run_id,
                    timestamp=now,
                    payload={"decision": decision},
                )
            ]

        self._mutate(run_id, mutate)

    def record_file_read(self, run_id: str, path: str) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.state.files_examined.append(path)
            record.telemetry.files_read.append(path)
            return [Event(type="file_read", run_id=run_id, timestamp=now, payload={"path": path})]

        self._mutate(run_id, mutate)

    def record_file_modified(self, run_id: str, path: str) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            if path not in record.state.files_modified:
                record.state.files_modified.append(path)
            if path not in record.telemetry.files_modified:
                record.telemetry.files_modified.append(path)
            return [
                Event(
                    type="file_modified",
                    run_id=run_id,
                    timestamp=now,
                    payload={"path": path},
                )
            ]

        self._mutate(run_id, mutate)

    def command_started(self, run_id: str, command: str) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.telemetry.commands.append(CommandRecord(command=command, started_at=now))
            return [
                Event(
                    type="command_started",
                    run_id=run_id,
                    timestamp=now,
                    payload={"command": command},
                )
            ]

        self._mutate(run_id, mutate)

    def command_finished(
        self,
        run_id: str,
        command: str,
        *,
        result: str | None = None,
        exit_code: int | None = None,
        success: bool | None = None,
    ) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            # Match the earliest-started open instance (FIFO) so that when the
            # same command string runs twice, results are attributed in order.
            # With no open instance (e.g. only the finish was reported), the
            # completion is recorded standalone rather than dropped.
            matched = False
            for item in record.telemetry.commands:
                if item.command == command and item.finished_at is None:
                    record.telemetry.commands.remove(item)
                    record.telemetry.commands.append(
                        CommandRecord(
                            command=item.command,
                            started_at=item.started_at,
                            finished_at=now,
                            exit_code=exit_code,
                            result=result,
                            success=success,
                        )
                    )
                    matched = True
                    break
            if not matched:
                record.telemetry.commands.append(
                    CommandRecord(
                        command=command,
                        started_at=now,
                        finished_at=now,
                        exit_code=exit_code,
                        result=result,
                        success=success,
                    )
                )
            return [
                Event(
                    type="command_finished",
                    run_id=run_id,
                    timestamp=now,
                    payload={
                        "command": command,
                        "exit_code": exit_code,
                        "success": success,
                        "result": result,
                    },
                )
            ]

        self._mutate(run_id, mutate)

    def transition_phase(self, run_id: str, target: EngineeringPhase | str) -> None:
        target_phase = EngineeringPhase(target)

        def mutate(record: RunRecord, now: str) -> list[Event]:
            previous = record.state.phase
            record.state.transition_to(target_phase)
            return [
                Event(
                    type="phase_changed",
                    run_id=run_id,
                    timestamp=now,
                    payload={"from": previous.value, "to": target_phase.value},
                )
            ]

        self._mutate(run_id, mutate)

    def raise_warning(
        self, run_id: str, kind: str, message: str, *, subject: str | None = None
    ) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.telemetry.warnings.append(
                WarningRecord(kind=kind, message=message, timestamp=now, subject=subject)
            )
            return [
                Event(
                    type="warning_raised",
                    run_id=run_id,
                    timestamp=now,
                    payload={"kind": kind, "message": message},
                )
            ]

        self._mutate(run_id, mutate)

    def record_failed_approach(self, run_id: str, approach: str) -> None:
        """Record an approach that was tried and failed."""

        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.state.failed_approaches.append(approach)
            return [
                Event(
                    type="decision_recorded",
                    run_id=run_id,
                    timestamp=now,
                    payload={"kind": "failed_approach", "decision": approach},
                )
            ]

        self._mutate(run_id, mutate)

    def verification_started(self, run_id: str, criterion: str) -> None:
        """Record that verification of a criterion has begun."""

        def mutate(record: RunRecord, now: str) -> list[Event]:
            return [
                Event(
                    type="verification_started",
                    run_id=run_id,
                    timestamp=now,
                    payload={"criterion": criterion},
                )
            ]

        self._mutate(run_id, mutate)

    def record_verification(
        self,
        run_id: str,
        criterion: str,
        status: str,
        *,
        evidence: tuple[str, ...] = (),
    ) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.telemetry.verification.append(
                VerificationRecord(
                    criterion=criterion,
                    status=status,
                    evidence=tuple(evidence),
                    timestamp=now,
                )
            )
            record.state.verification[criterion] = _status_to_bool(status)
            return [
                Event(
                    type="verification_result",
                    run_id=run_id,
                    timestamp=now,
                    payload={
                        "criterion": criterion,
                        "status": status,
                        "evidence": list(evidence),
                    },
                )
            ]

        self._mutate(run_id, mutate)

    def verify(
        self,
        run_id: str,
        *,
        timeout: float = 600.0,
        checks: tuple[DiscoveredCheck, ...] | None = None,
    ) -> VerificationReport:
        """Independently verify the run and persist evidence to the record.

        Runs the repository's own discovered checks, maps outcomes to
        acceptance criteria, records executed checks as commands, and appends
        one ``verification_result`` event per criterion.
        """
        record = self.get(run_id)
        report = Verifier(self.repo_root, timeout=timeout).verify(record, checks=checks)
        fingerprint = capture_fingerprint(self.repo_root)

        def mutate(rec: RunRecord, now: str) -> list[Event]:
            rec.telemetry.last_verification_outcome = report.outcome
            rec.telemetry.verification_snapshot = VerificationSnapshot(
                event_sequence=self.db.max_sequence(run_id),
                verified_at=now,
                git_head=fingerprint.git_head,
                diff_hash=fingerprint.diff_hash,
                outcome=report.outcome,
            )
            events: list[Event] = [
                Event(
                    type="verification_started",
                    run_id=run_id,
                    timestamp=now,
                    payload={"criterion": "*"},
                )
            ]
            for result in report.checks:
                rec.telemetry.commands.append(
                    CommandRecord(
                        command=result.check.command,
                        started_at=now,
                        finished_at=now,
                        exit_code=result.exit_code,
                        result=result.output_tail,
                        success=result.success,
                    )
                )
            for item in report.criteria:
                rec.telemetry.verification.append(
                    VerificationRecord(
                        criterion=item.criterion,
                        status=item.status,
                        evidence=item.evidence,
                        timestamp=now,
                    )
                )
                rec.state.verification[item.criterion] = _status_to_bool(item.status)
                events.append(
                    Event(
                        type="verification_result",
                        run_id=run_id,
                        timestamp=now,
                        payload={
                            "criterion": item.criterion,
                            "status": item.status,
                            "evidence": list(item.evidence),
                        },
                    )
                )
            return events

        self._mutate(run_id, mutate)
        return report

    # -- reads ---------------------------------------------------------------

    def get(self, run_id: str) -> RunRecord:
        record = self.db.load_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    # -- active run ------------------------------------------------------------

    def _write_active_run(self, run_id: str) -> None:
        """Point observation hooks at this run (newest run wins)."""
        self.sogi_dir.mkdir(parents=True, exist_ok=True)
        (self.sogi_dir / "active_run").write_text(run_id + "\n", encoding="utf-8")

    def active_run_id(self) -> str | None:
        """Resolve the run observation events should attach to.

        Prefers the explicitly recorded active run; falls back to the most
        recently created run that is not DONE. Returns None when nothing is
        in progress so hooks can no-op silently.
        """
        marker = self.sogi_dir / "active_run"
        if marker.is_file():
            try:
                candidate = marker.read_text(encoding="utf-8").strip()
            except OSError:
                candidate = ""
            if candidate:
                try:
                    record = self.get(candidate)
                    if record.state.phase is not EngineeringPhase.DONE:
                        return candidate
                except RunNotFoundError:
                    pass
        for record in reversed(self.db.list_runs()):
            if record.state.phase is not EngineeringPhase.DONE:
                return record.run_id
        return None

    def render(self, run_id: str) -> str:
        return render_run_state(self.get(run_id))

    def render_start(self, run_id: str) -> str:
        return render_run_start(self.get(run_id))

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> RunService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals -----------------------------------------------------------

    def _provider_for(self) -> RepositoryProvider:
        if self._provider is None:
            self._provider = TreeSitterProvider(self.repo_root, command=self._analyzer_command)
        return self._provider

    def _new_run_id(self) -> str:
        for _ in range(10):
            candidate = secrets.token_hex(3)
            if self.db.load_run(candidate) is None:
                return candidate
        raise RuntimeError("Could not allocate a unique run id")

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Serialize read-modify-write across RunService instances (processes).

        The in-process ``self._lock`` only guards one service instance; the MCP
        server and a CLI process can both hold a RunService on the same repo.
        An exclusive flock on ``.sogi/lock`` makes the load-mutate-save cycle
        atomic across processes so no mutation is lost.
        """
        self.sogi_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.sogi_dir / "lock"
        with self._lock, open(lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _mutate(self, run_id: str, fn: Callable[[RunRecord, str], list[Event]]) -> None:
        with self._file_lock():
            record = self.get(run_id)
            now = _now()
            events = fn(record, now)
            events.extend(self._govern(record, events, now))
            record.state.updated_at = now
            record.updated_at = now
            # Projection + events commit in one transaction: a crash cannot
            # leave the event stream and the run snapshot inconsistent.
            self.db.save_run_with_events(record, events)

    def _govern(self, record: RunRecord, new_events: list[Event], now: str) -> list[Event]:
        """Run deterministic checks and turn new findings into warnings.

        Findings whose kind+subject already has a recorded warning are skipped,
        so an ongoing loop produces one intervention rather than one per step.
        """
        stream = self.events.for_run(record.run_id) + new_events
        findings = self.governor.inspect(record, stream)
        warned = {
            f"{warning.kind}:{warning.subject}"
            for warning in record.telemetry.warnings
            if warning.subject is not None
        }
        events: list[Event] = []
        for finding in findings:
            if finding.signature in warned:
                continue
            record.telemetry.warnings.append(
                WarningRecord(
                    kind=finding.kind,
                    message=finding.message,
                    timestamp=now,
                    subject=finding.subject,
                    severity=finding.severity,
                )
            )
            warned.add(finding.signature)
            events.append(
                Event(
                    type="warning_raised",
                    run_id=record.run_id,
                    timestamp=now,
                    payload={
                        "kind": finding.kind,
                        "message": finding.message,
                        "severity": finding.severity,
                    },
                )
            )
        return events


def _status_to_bool(status: str) -> bool | None:
    if status == "SATISFIED":
        return True
    if status == "VIOLATED":
        return False
    return None
