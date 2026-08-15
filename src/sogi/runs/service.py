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
    WarningRecord,
)
from sogi.core.task_spec import TaskSpec
from sogi.events.event import Event
from sogi.events.log import EventLog
from sogi.repository.provider import RepositoryProvider
from sogi.repository.tree_sitter_provider import AnalyzerCommandError, TreeSitterProvider
from sogi.state.engineering_state import EngineeringState
from sogi.storage.db import SogiDatabase

from .render import render_run_start, render_run_state


class RunNotFoundError(KeyError):
    """Raised when a run_id does not exist in the store."""


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
            self.db.save_run(record)
            self.events.append(
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
            )
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

    def complete(self, run_id: str) -> RunRecord:
        """Mark a run complete. Completion is terminal and reachable from any phase."""

        def mutate(record: RunRecord, now: str) -> list[Event]:
            events: list[Event] = []
            if record.state.phase != EngineeringPhase.DONE:
                events.append(
                    Event(
                        type="phase_changed",
                        run_id=run_id,
                        timestamp=now,
                        payload={
                            "from": record.state.phase.value,
                            "to": EngineeringPhase.DONE.value,
                        },
                    )
                )
                record.state.phase = EngineeringPhase.DONE
            record.telemetry.completed_at = now
            events.append(Event(type="run_completed", run_id=run_id, timestamp=now))
            return events

        self._mutate(run_id, mutate)
        return self.get(run_id)

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
            return [
                Event(type="file_read", run_id=run_id, timestamp=now, payload={"path": path})
            ]

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
            record.telemetry.commands.append(
                CommandRecord(command=command, started_at=now)
            )
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
                    break
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

    def raise_warning(self, run_id: str, kind: str, message: str) -> None:
        def mutate(record: RunRecord, now: str) -> list[Event]:
            record.telemetry.warnings.append(
                WarningRecord(kind=kind, message=message, timestamp=now)
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

    # -- reads ---------------------------------------------------------------

    def get(self, run_id: str) -> RunRecord:
        record = self.db.load_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    def render(self, run_id: str) -> str:
        return render_run_state(self.get(run_id))

    def render_start(self, run_id: str) -> str:
        return render_run_start(self.get(run_id))

    def close(self) -> None:
        self.db.close()

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
            record.state.updated_at = now
            record.updated_at = now
            for event in events:
                self.events.append(event)
            self.db.save_run(record)


def _status_to_bool(status: str) -> bool | None:
    if status == "SATISFIED":
        return True
    if status == "VIOLATED":
        return False
    return None
