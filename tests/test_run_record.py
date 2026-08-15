from sogi.core.phases import EngineeringPhase
from sogi.core.run_record import (
    SATISFIED,
    UNVERIFIED,
    VIOLATED,
    CommandRecord,
    RunRecord,
    Telemetry,
    VerificationRecord,
    WarningRecord,
)
from sogi.core.task_spec import TaskSpec
from sogi.state.engineering_state import EngineeringState


def _record() -> RunRecord:
    task = TaskSpec.from_prompt(
        "Fix expired refresh token redirect",
        acceptance_criteria=("Redirect to /login",),
        constraints=("Preserve OAuth",),
    )
    state = EngineeringState(task_id="abc123", objective=task.objective)
    state.transition_to(EngineeringPhase.INVESTIGATE)
    return RunRecord(run_id="abc123", task=task, state=state)


def test_run_record_round_trip() -> None:
    record = _record()
    record.state.decisions.append("Handle expiration in refresh middleware")
    record.telemetry.files_read.append("src/auth.py")
    record.telemetry.commands.append(
        CommandRecord(command="pytest", started_at="2026-01-01T00:00:00+00:00")
    )
    record.telemetry.warnings.append(
        WarningRecord(kind="repeated_read", message="auth.py inspected 4 times")
    )
    record.telemetry.verification.append(
        VerificationRecord(criterion="Redirect to /login", status=SATISFIED)
    )

    restored = RunRecord.from_dict(record.to_dict())

    assert restored.to_dict() == record.to_dict()
    assert restored.task.concepts == record.task.concepts
    assert restored.state.phase == EngineeringPhase.INVESTIGATE


def test_run_record_round_trip_without_context() -> None:
    record = _record()
    assert record.context is None
    restored = RunRecord.from_dict(record.to_dict())
    assert restored.context is None


def test_verification_statuses_are_non_binary() -> None:
    assert SATISFIED != VIOLATED != UNVERIFIED
    for status in (SATISFIED, VIOLATED, UNVERIFIED):
        VerificationRecord(criterion="c", status=status)


def test_verification_rejects_unknown_status() -> None:
    try:
        VerificationRecord(criterion="c", status="PASS")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown status")


def test_telemetry_round_trip() -> None:
    telemetry = Telemetry(context_budget=1200)
    telemetry.context_tokens.append(243)
    telemetry.context_compilations = 1
    telemetry.completed_at = "2026-01-01T00:00:00+00:00"

    restored = Telemetry.from_dict(telemetry.to_dict())

    assert restored.context_budget == 1200
    assert restored.context_tokens == [243]
    assert restored.context_compilations == 1
    assert restored.completed_at == telemetry.completed_at
