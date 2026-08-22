from pathlib import Path

import pytest
from fakes import FakeProvider, exit_command

from sogi.core.phases import EngineeringPhase
from sogi.runs.service import RunNotFoundError, RunService


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture()
def service(repo: Path) -> RunService:
    return RunService(repo, provider=FakeProvider(repo))


def test_start_creates_persisted_run(service: RunService) -> None:
    record = service.start("Fix expired refresh token redirect", compile_context=False)

    assert record.run_id
    assert record.state.phase == EngineeringPhase.UNDERSTAND
    assert record.task.objective == "Fix expired refresh token redirect"
    assert (service.sogi_dir / "sogi.db").exists()
    assert (service.sogi_dir / "runs" / f"{record.run_id}.json").exists()

    events = service.events.for_run(record.run_id)
    assert [event.type for event in events] == ["task_created"]


def test_start_compiles_context_and_advances_phase(service: RunService) -> None:
    record = service.start(
        "Fix expired refresh token redirect",
        acceptance_criteria=("Redirect to /login",),
        budget=1200,
    )

    assert record.context is not None
    assert record.context.selected_tokens <= 1200
    assert record.state.phase == EngineeringPhase.INVESTIGATE
    assert record.telemetry.context_compilations == 1
    assert record.telemetry.context_tokens == [record.context.selected_tokens]

    types = [event.type for event in service.events.for_run(record.run_id)]
    assert types == ["task_created", "context_compiled", "phase_changed"]


def test_start_without_context_keeps_understand_phase(service: RunService) -> None:
    record = service.start("Fix auth", compile_context=False)
    assert record.state.phase == EngineeringPhase.UNDERSTAND
    assert record.context is None


def test_get_unknown_run_raises(service: RunService) -> None:
    with pytest.raises(RunNotFoundError):
        service.get("nope")


def test_record_decision_appends_state_and_event(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.record_decision(run_id, "Handle expiration in refresh middleware")

    record = service.get(run_id)
    assert record.state.decisions == ["Handle expiration in refresh middleware"]
    event = service.events.for_run(run_id)[-1]
    assert event.type == "decision_recorded"
    assert event.payload["decision"] == "Handle expiration in refresh middleware"


def test_file_read_and_modified(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.record_file_read(run_id, "src/auth.py")
    service.record_file_read(run_id, "src/auth.py")
    service.record_file_modified(run_id, "src/auth.py")
    service.record_file_modified(run_id, "src/auth.py")

    record = service.get(run_id)
    assert record.state.files_examined == ["src/auth.py", "src/auth.py"]
    assert record.state.files_modified == ["src/auth.py"]
    assert record.telemetry.files_read == ["src/auth.py", "src/auth.py"]
    assert record.telemetry.files_modified == ["src/auth.py"]


def test_command_started_and_finished(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.command_started(run_id, "pytest tests/auth/test_refresh.py")
    service.command_finished(
        run_id, "pytest tests/auth/test_refresh.py", exit_code=1, success=False
    )

    record = service.get(run_id)
    assert len(record.telemetry.commands) == 1
    command = record.telemetry.commands[0]
    assert command.command == "pytest tests/auth/test_refresh.py"
    assert command.exit_code == 1
    assert command.success is False
    assert command.finished_at is not None

    types = [event.type for event in service.events.for_run(run_id)]
    assert types == ["task_created", "command_started", "command_finished"]


def test_transition_phase_enforces_lifecycle(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.transition_phase(run_id, EngineeringPhase.INVESTIGATE)
    assert service.get(run_id).state.phase == EngineeringPhase.INVESTIGATE

    with pytest.raises(ValueError):
        service.transition_phase(run_id, EngineeringPhase.DONE)

    event = service.events.for_run(run_id)[-1]
    assert event.type == "phase_changed"
    assert event.payload == {"from": "understand", "to": "investigate"}


def test_raise_warning_records_intervention(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.raise_warning(run_id, "repeated_read", "auth.py inspected 4 times")

    record = service.get(run_id)
    assert record.telemetry.warnings[0].kind == "repeated_read"
    event = service.events.for_run(run_id)[-1]
    assert event.type == "warning_raised"


def test_record_failed_approach(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.record_failed_approach(run_id, "Tried patching validate_token")

    record = service.get(run_id)
    assert record.state.failed_approaches == ["Tried patching validate_token"]
    event = service.events.for_run(run_id)[-1]
    assert event.type == "decision_recorded"
    assert event.payload == {"kind": "failed_approach", "decision": "Tried patching validate_token"}


def test_verification_started_emits_event(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.verification_started(run_id, "Expired token redirects to /login")

    event = service.events.for_run(run_id)[-1]
    assert event.type == "verification_started"
    assert event.payload == {"criterion": "Expired token redirects to /login"}


def test_record_verification_maps_to_state(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.record_verification(
        run_id,
        "Expired token redirects to /login",
        "SATISFIED",
        evidence=("tests/auth/test_refresh.py::test_expired_redirect",),
    )

    record = service.get(run_id)
    assert record.state.verification["Expired token redirects to /login"] is True
    assert record.telemetry.verification[0].status == "SATISFIED"
    assert record.telemetry.verification[0].evidence == (
        "tests/auth/test_refresh.py::test_expired_redirect",
    )


def test_complete_requires_verification_evidence(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    from sogi.runs.service import CompletionGateError

    with pytest.raises(CompletionGateError) as excinfo:
        service.complete(run_id)
    assert "No independent verification has run" in str(excinfo.value)


def test_complete_blocked_by_failed_verification(repo: Path) -> None:
    from fakes import FakeProvider as _FP  # noqa: F401

    from sogi.runs.service import CompletionGateError
    from sogi.verification.discovery import DiscoveredCheck

    service = RunService(repo)
    run_id = service.start("Fix auth", compile_context=False).run_id

    failing = DiscoveredCheck(name="t", command=exit_command(1), kind="test")
    report = service.verify(run_id, checks=(failing,))
    # An objectively failing repository check is FAIL, not INCONCLUSIVE,
    # even when no acceptance criteria exist to map evidence onto.
    assert report.outcome == "FAIL"
    assert service.get(run_id).telemetry.last_verification_outcome == "FAIL"

    with pytest.raises(CompletionGateError):
        service.complete(run_id)


def test_complete_is_terminal_from_any_phase_with_force(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    record = service.complete(run_id, force=True)

    assert record.state.phase == EngineeringPhase.DONE
    assert record.telemetry.completed_at is not None
    assert record.telemetry.outcome == "completion_forced"
    kinds = [warning.kind for warning in record.telemetry.warnings]
    assert "completion_forced" in kinds
    types = [event.type for event in service.events.for_run(run_id)]
    assert types == ["task_created", "warning_raised", "phase_changed", "run_completed"]


def test_complete_passes_gate_after_successful_verification(service: RunService) -> None:
    from sogi.verification.discovery import DiscoveredCheck

    run_id = service.start(
        "Fix auth",
        acceptance_criteria=("Auth behavior works",),
        compile_context=False,
    ).run_id

    # No verification yet: gated.
    from sogi.runs.service import CompletionGateError

    with pytest.raises(CompletionGateError):
        service.complete(run_id)

    passing = DiscoveredCheck(name="t", command=exit_command(0), kind="test")
    service.verify(run_id, checks=(passing,))

    # Context was never compiled, so the criterion maps as unverified; the
    # explicit policy decision accepts it.
    record = service.complete(run_id, allow_unverified=True)
    assert record.state.phase == EngineeringPhase.DONE
    assert record.telemetry.outcome == "completed_with_unverified"
    assert service.active_run_id() is None


def test_persistence_across_service_instances(repo: Path) -> None:
    first = RunService(repo, provider=FakeProvider(repo))
    run_id = first.start("Fix auth", compile_context=False).run_id
    first.record_decision(run_id, "Use middleware")
    first.record_file_modified(run_id, "src/auth.py")
    first.close()

    second = RunService(repo, provider=FakeProvider(repo))
    record = second.get(run_id)

    assert record.state.decisions == ["Use middleware"]
    assert record.state.files_modified == ["src/auth.py"]
    assert [event.type for event in second.events.for_run(run_id)] == [
        "task_created",
        "decision_recorded",
        "file_modified",
    ]


def test_concurrent_mutations_across_instances_do_not_lose_updates(repo: Path) -> None:
    import threading

    first = RunService(repo, provider=FakeProvider(repo))
    run_id = first.start("Fix auth", compile_context=False).run_id
    first.close()

    def record(decision: str) -> None:
        service = RunService(repo, provider=FakeProvider(repo))
        service.record_decision(run_id, decision)
        service.close()

    threads = [threading.Thread(target=record, args=(f"decision-{index}",)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final = RunService(repo, provider=FakeProvider(repo)).get(run_id)
    assert sorted(final.state.decisions) == [f"decision-{index}" for index in range(5)]


def test_list_runs_returns_all_in_creation_order(service: RunService) -> None:
    first = service.start("Fix auth", compile_context=False).run_id
    second = service.start("Add billing", compile_context=False).run_id

    records = service.db.list_runs()

    assert [record.run_id for record in records] == [first, second]


def test_run_ids_are_unique(service: RunService) -> None:
    run_ids = {service.start("Fix auth", compile_context=False).run_id for _ in range(20)}
    assert len(run_ids) == 20


def test_repo_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RunService(tmp_path / "missing")


def test_complete_rejected_when_files_change_after_verify(repo: Path) -> None:
    """The verify -> edit -> complete sequence must be rejected as stale."""
    from fakes import FakeProvider

    from sogi.runs.service import RunService
    from sogi.verification.discovery import DiscoveredCheck

    service = RunService(repo, provider=FakeProvider(repo))
    run_id = service.start(
        "Fix auth",
        acceptance_criteria=("Auth behavior works",),
        compile_context=False,
    ).run_id

    passing = DiscoveredCheck(name="t", command=exit_command(0), kind="test")
    service.verify(run_id, checks=(passing,))

    # The agent edits after verification: evidence is now stale.
    service.record_file_modified(run_id, "src/auth.py")

    from sogi.runs.service import CompletionGateError as CGE

    with pytest.raises(CGE) as excinfo:
        service.complete(run_id, allow_unverified=True)
    assert "stale" in str(excinfo.value)

    # Re-verifying refreshes the watermark and completion succeeds.
    service.verify(run_id, checks=(passing,))
    record = service.complete(run_id, allow_unverified=True)
    assert record.telemetry.outcome == "completed_with_unverified"


def test_verification_snapshot_records_watermark(service: RunService) -> None:
    from sogi.verification.discovery import DiscoveredCheck

    run_id = service.start("Fix auth", compile_context=False).run_id
    service.record_file_modified(run_id, "src/auth.py")

    service.verify(
        run_id,
        checks=(DiscoveredCheck(name="t", command=exit_command(0), kind="test"),),
    )

    snapshot = service.get(run_id).telemetry.verification_snapshot
    assert snapshot is not None
    # Watermark sits at the file_modified event: task_created(1), file_modified(2).
    assert snapshot.event_sequence == 2
    assert snapshot.outcome in {"PASS", "PASS_WITH_UNVERIFIED"}
