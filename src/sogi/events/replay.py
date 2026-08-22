"""Deterministic event replay.

The event log is the source of truth; :func:`reduce_event` projects it into a
``RunRecord`` without consulting stored snapshots. ``sogi run rebuild``
reconstructs a run purely from its stream, and ``check-integrity`` compares
the projection against the stored snapshot so drift between the two is
detectable instead of silent.

Projection honesty: some rich fields (compiled context bodies, verification
snapshots, patch assessments) live only in snapshots because their events
carry summaries. The reducer reproduces every field the stream determines;
integrity checks compare exactly those fields and report the rest as
snapshot-only.
"""

from __future__ import annotations

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
from sogi.state.engineering_state import EngineeringState

#: Fields that only exist in stored snapshots (not derivable from events).
SNAPSHOT_ONLY_FIELDS = (
    "context",
    "telemetry.verification_snapshot",
    "telemetry.patch_assessment",
    "telemetry.last_verification_outcome",
)


def reduce_event(record: RunRecord | None, event: Event) -> RunRecord:
    """Apply one event to a record (or None) and return the new record."""
    payload = event.payload
    kind = event.type

    if kind == "task_created":
        task = TaskSpec.from_prompt(
            str(payload.get("objective", "")),
            acceptance_criteria=tuple(payload.get("acceptance_criteria", ())),
            constraints=tuple(payload.get("constraints", ())),
        )
        budget = int(payload.get("budget", 4000))
        return RunRecord(
            run_id=event.run_id,
            task=task,
            state=EngineeringState(
                task_id=event.run_id,
                objective=task.objective,
                constraints=list(task.constraints),
                created_at=event.timestamp,
                updated_at=event.timestamp,
            ),
            telemetry=Telemetry(
                context_budget=budget,
                started_at=event.timestamp,
            ),
            created_at=event.timestamp,
            updated_at=event.timestamp,
        )

    if record is None:
        raise ValueError(f"First event for {event.run_id} must be task_created, got {kind!r}")

    # reduce_event mutates in place by design: replay applies the whole
    # stream sequentially and the intermediate records are never shared.
    if kind == "file_read":
        path = str(payload.get("path"))
        record.state.files_examined.append(path)
        record.telemetry.files_read.append(path)

    elif kind == "file_modified":
        path = str(payload.get("path"))
        if path not in record.state.files_modified:
            record.state.files_modified.append(path)
        if path not in record.telemetry.files_modified:
            record.telemetry.files_modified.append(path)

    elif kind == "decision_recorded":
        if payload.get("kind") == "failed_approach":
            record.state.failed_approaches.append(str(payload.get("decision")))
        elif payload.get("kind") == "acknowledge":
            key = f"{payload.get('warning_kind')}:{payload.get('subject')}"
            record.state.acknowledged[key] = event.timestamp
        else:
            record.state.decisions.append(str(payload.get("decision")))

    elif kind == "warning_raised":
        subject = payload.get("subject")
        record.telemetry.warnings.append(
            WarningRecord(
                kind=str(payload.get("kind")),
                message=str(payload.get("message")),
                timestamp=event.timestamp,
                subject=str(subject) if subject is not None else None,
                severity=str(payload.get("severity", "WARNING")),
            )
        )

    elif kind == "command_started":
        record.telemetry.commands.append(
            CommandRecord(command=str(payload.get("command")), started_at=event.timestamp)
        )

    elif kind == "command_finished":
        _apply_command_finish(record, payload, event)

    elif kind == "context_compiled":
        record.telemetry.context_compilations += 1
        record.telemetry.context_budget = int(
            payload.get("budget", record.telemetry.context_budget)
        )
        record.telemetry.context_tokens.append(int(payload.get("selected_tokens", 0)))

    elif kind == "phase_changed":
        record.state.phase = EngineeringPhase(str(payload.get("to")))

    elif kind == "verification_started":
        pass  # marker only

    elif kind == "verification_result":
        evidence = tuple(payload.get("evidence", ()))
        status = str(payload.get("status"))
        criterion = str(payload.get("criterion"))
        record.telemetry.verification.append(
            VerificationRecord(
                criterion=criterion, status=status, evidence=evidence, timestamp=event.timestamp
            )
        )
        record.state.verification[criterion] = _status_to_bool(status)

    elif kind == "run_completed":
        record.state.phase = EngineeringPhase.DONE
        record.telemetry.completed_at = event.timestamp

    else:
        raise ValueError(f"Unknown event type during replay: {kind!r}")

    record.state.updated_at = event.timestamp
    record.updated_at = event.timestamp
    return record


def _apply_command_finish(record: RunRecord, payload: dict, event: Event) -> None:
    """Mirror RunService.command_finished attribution (FIFO open instance)."""
    command = str(payload.get("command"))
    exit_code = payload.get("exit_code")
    success = payload.get("success")
    result = payload.get("result")
    for item in record.telemetry.commands:
        if item.command == command and item.finished_at is None:
            record.telemetry.commands.remove(item)
            record.telemetry.commands.append(
                CommandRecord(
                    command=item.command,
                    started_at=item.started_at,
                    finished_at=event.timestamp,
                    exit_code=int(exit_code) if exit_code is not None else None,
                    result=str(result) if result is not None else None,
                    success=success if isinstance(success, bool) else None,
                )
            )
            return
    record.telemetry.commands.append(
        CommandRecord(
            command=command,
            started_at=event.timestamp,
            finished_at=event.timestamp,
            exit_code=int(exit_code) if exit_code is not None else None,
            result=str(result) if result is not None else None,
            success=success if isinstance(success, bool) else None,
        )
    )


def replay(events: list[Event]) -> RunRecord:
    """Reconstruct a full run from its ordered event stream."""
    record: RunRecord | None = None
    for event in sorted(events, key=lambda item: item.sequence):
        record = reduce_event(record, event)
    if record is None:
        raise ValueError("Cannot replay an empty event stream")
    return record


def _status_to_bool(status: str) -> bool | None:
    if status == "SATISFIED":
        return True
    if status == "VIOLATED":
        return False
    return None


def compare_with_snapshot(replayed: RunRecord, stored: RunRecord) -> dict[str, list[str]]:
    """Compare stream-derived state against the stored snapshot.

    Returns {"mismatches": [...], "snapshot_only": [...]} — mismatches are
    real inconsistencies; snapshot-only entries are fields the stream cannot
    carry and are informational.
    """
    mismatches: list[str] = []

    def check(label: str, left: object, right: object) -> None:
        if left != right:
            mismatches.append(label)

    check("state.phase", replayed.state.phase, stored.state.phase)
    check("state.decisions", replayed.state.decisions, stored.state.decisions)
    check(
        "state.failed_approaches", replayed.state.failed_approaches, stored.state.failed_approaches
    )
    check("state.files_modified", replayed.state.files_modified, stored.state.files_modified)
    check(
        "state.acknowledged.keys",
        sorted(replayed.state.acknowledged),
        sorted(stored.state.acknowledged),
    )
    check("telemetry.files_read", replayed.telemetry.files_read, stored.telemetry.files_read)
    check(
        "telemetry.files_modified",
        replayed.telemetry.files_modified,
        stored.telemetry.files_modified,
    )
    check("telemetry.commands", replayed.telemetry.commands, stored.telemetry.commands)
    check("telemetry.warnings", replayed.telemetry.warnings, stored.telemetry.warnings)
    check(
        "telemetry.context_compilations",
        replayed.telemetry.context_compilations,
        stored.telemetry.context_compilations,
    )
    check(
        "telemetry.verification",
        replayed.telemetry.verification,
        stored.telemetry.verification,
    )

    snapshot_only: list[str] = []
    if stored.context is not None:
        snapshot_only.append("context (compiled bodies are not carried by events)")
    if stored.telemetry.verification_snapshot is not None:
        snapshot_only.append("telemetry.verification_snapshot")
    if stored.telemetry.patch_assessment is not None:
        snapshot_only.append("telemetry.patch_assessment")

    return {"mismatches": mismatches, "snapshot_only": snapshot_only}
