"""Tests for the deterministic Engineering Governor."""

from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.core.phases import EngineeringPhase
from sogi.core.run_record import RunRecord
from sogi.core.task_spec import TaskSpec
from sogi.events.event import Event
from sogi.governor import Governor
from sogi.governor.failures import check_failure_loops
from sogi.governor.reads import check_repeated_reads
from sogi.governor.scope import check_scope_expansion
from sogi.runs.service import RunService
from sogi.state.engineering_state import EngineeringState


def make_record(objective: str = "Fix expired refresh token redirect") -> RunRecord:
    task = TaskSpec.from_prompt(objective)
    state = EngineeringState(task_id="run1", objective=task.objective)
    return RunRecord(run_id="run1", task=task, state=state)


def event(type_: str, **payload: str) -> Event:
    return Event(type=type_, run_id="run1", payload=payload)


# -- repeated reads -----------------------------------------------------------


def test_no_finding_below_threshold() -> None:
    events = [event("file_read", path="src/auth.py") for _ in range(2)]
    assert check_repeated_reads(events) == []


def test_finding_at_threshold() -> None:
    events = [event("file_read", path="src/auth.py") for _ in range(3)]
    findings = check_repeated_reads(events)
    assert len(findings) == 1
    assert findings[0].kind == "repeated_read"
    assert findings[0].subject == "src/auth.py"
    assert findings[0].signature == "repeated_read:src/auth.py"


def test_four_reads_produce_one_finding() -> None:
    events = [event("file_read", path="src/auth.py") for _ in range(4)]
    assert len(check_repeated_reads(events)) == 1


def test_modification_resets_read_counter() -> None:
    events = [
        event("file_read", path="src/auth.py"),
        event("file_read", path="src/auth.py"),
        event("file_modified", path="src/auth.py"),
        event("file_read", path="src/auth.py"),
    ]
    assert check_repeated_reads(events) == []


def test_different_paths_do_not_accumulate() -> None:
    events = [
        event("file_read", path="src/a.py"),
        event("file_read", path="src/b.py"),
        event("file_read", path="src/c.py"),
    ]
    assert check_repeated_reads(events) == []


# -- failure loops ------------------------------------------------------------


def test_failure_loop_at_threshold() -> None:
    events = [
        event("command_finished", command="pytest tests/test_auth.py", success=False)
        for _ in range(3)
    ]
    findings = check_failure_loops(events)
    assert len(findings) == 1
    assert findings[0].kind == "failure_loop"
    assert findings[0].subject == "pytest tests/test_auth.py"


def test_success_resets_failure_streak() -> None:
    events = [
        event("command_finished", command="pytest", success=False),
        event("command_finished", command="pytest", success=False),
        event("command_finished", command="pytest", success=True),
        event("command_finished", command="pytest", success=False),
        event("command_finished", command="pytest", success=False),
    ]
    assert check_failure_loops(events) == []


def test_file_modification_resets_failure_streak() -> None:
    events = [
        event("command_finished", command="pytest", success=False),
        event("command_finished", command="pytest", success=False),
        event("file_modified", path="src/auth.py"),
        event("command_finished", command="pytest", success=False),
    ]
    assert check_failure_loops(events) == []


def test_different_commands_do_not_accumulate() -> None:
    events = [
        event("command_finished", command="pytest a.py", success=False),
        event("command_finished", command="pytest b.py", success=False),
        event("command_finished", command="pytest c.py", success=False),
    ]
    assert check_failure_loops(events) == []


def test_unfinished_or_unknown_success_ignored() -> None:
    events = [
        event("command_started", command="pytest"),
        event("command_finished", command="pytest", exit_code=0),
    ]
    assert check_failure_loops(events) == []


# -- scope expansion ----------------------------------------------------------


def context_record(context_files: tuple[str, ...] = ("src/auth.py", "tests/test_auth.py")):
    record = make_record()
    from sogi.context.compiler import CompiledContext

    record.context = CompiledContext(
        task=record.task,
        selected=(),
        related_files=context_files,
        related_tests=("tests/test_auth.py",),
        repository_estimated_tokens=100,
        candidate_tokens=50,
        selected_tokens=40,
        token_budget=4000,
        suggested_next_investigation="",
    )
    return record


def test_in_scope_modification_not_flagged() -> None:
    record = context_record()
    findings = check_scope_expansion(record, [event("file_modified", path="src/auth.py")])
    assert findings == []


def test_out_of_scope_modification_flagged_once() -> None:
    record = context_record()
    events = [
        event("file_modified", path="billing/charge.py"),
        event("file_modified", path="billing/charge.py"),
    ]
    findings = check_scope_expansion(record, events)
    assert len(findings) == 1
    assert findings[0].kind == "scope_expansion"
    assert findings[0].subject == "billing/charge.py"


def test_sibling_directory_of_related_test_is_in_scope() -> None:
    record = context_record()
    findings = check_scope_expansion(
        record, [event("file_modified", path="tests/helpers/auth_util.py")]
    )
    assert findings == []


def test_concept_match_is_in_scope() -> None:
    record = context_record()
    findings = check_scope_expansion(
        record, [event("file_modified", path="docs/refresh-tokens.md")]
    )
    assert findings == []


def test_no_context_means_no_scope_check() -> None:
    record = make_record()
    assert record.context is None
    findings = check_scope_expansion(record, [event("file_modified", path="billing/x.py")])
    assert findings == []


# -- engine --------------------------------------------------------------------


def test_engine_combines_all_checks() -> None:
    record = context_record()
    events = [
        *([event("file_read", path="src/auth.py")] * 3),
        *([event("command_finished", command="pytest", success=False)] * 3),
        event("file_modified", path="billing/charge.py"),
    ]
    kinds = {finding.kind for finding in Governor().inspect(record, events)}
    assert kinds == {"repeated_read", "failure_loop", "scope_expansion"}


def test_engine_silent_for_done_runs() -> None:
    record = context_record()
    record.state.phase = EngineeringPhase.DONE
    events = [
        *([event("file_read", path="src/auth.py")] * 5),
        *([event("command_finished", command="pytest", success=False)] * 5),
    ]
    assert Governor().inspect(record, events) == ()


# -- RunService integration ------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture()
def service(repo: Path) -> RunService:
    return RunService(repo, provider=FakeProvider(repo))


def test_service_raises_repeated_read_warning(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    for _ in range(3):
        service.record_file_read(run_id, "src/auth.py")

    record = service.get(run_id)
    warnings = [warning for warning in record.telemetry.warnings if warning.kind == "repeated_read"]
    assert len(warnings) == 1
    assert warnings[0].subject == "src/auth.py"
    types = [item.type for item in service.events.for_run(run_id)]
    assert types.count("warning_raised") == 1


def test_service_does_not_duplicate_warnings(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    for _ in range(6):
        service.record_file_read(run_id, "src/auth.py")

    warnings = service.get(run_id).telemetry.warnings
    assert sum(warning.kind == "repeated_read" for warning in warnings) == 1


def test_service_reset_by_modification(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.record_file_read(run_id, "src/auth.py")
    service.record_file_read(run_id, "src/auth.py")
    service.record_file_modified(run_id, "src/auth.py")
    service.record_file_read(run_id, "src/auth.py")

    assert not [w for w in service.get(run_id).telemetry.warnings if w.subject]


def test_service_raises_failure_loop_warning(service: RunService) -> None:
    run_id = service.start("Fix auth", compile_context=False).run_id

    for _ in range(3):
        service.command_started(run_id, "pytest tests/")
        service.command_finished(run_id, "pytest tests/", exit_code=1, success=False)

    warnings = service.get(run_id).telemetry.warnings
    loop = [warning for warning in warnings if warning.kind == "failure_loop"]
    assert len(loop) == 1
    assert loop[0].subject == "pytest tests/"


def test_service_raises_scope_warning_against_compiled_context(service: RunService) -> None:
    run_id = service.start("Fix expired refresh token redirect").run_id
    assert service.get(run_id).context is not None

    service.record_file_modified(run_id, "billing/charge.py")

    warnings = service.get(run_id).telemetry.warnings
    expansion = [warning for warning in warnings if warning.kind == "scope_expansion"]
    assert len(expansion) == 1
    assert expansion[0].subject == "billing/charge.py"


def test_service_no_scope_warning_for_related_paths(service: RunService) -> None:
    run_id = service.start("Fix expired refresh token redirect").run_id

    service.record_file_modified(run_id, "src/auth.py")
    service.record_file_modified(run_id, "tests/test_auth.py")

    assert not [w for w in service.get(run_id).telemetry.warnings if w.subject]


def test_service_persists_governor_events_across_instances(repo: Path) -> None:
    first = RunService(repo, provider=FakeProvider(repo))
    run_id = first.start("Fix auth", compile_context=False).run_id
    for _ in range(3):
        first.record_file_read(run_id, "src/auth.py")
    first.close()

    second = RunService(repo, provider=FakeProvider(repo))
    types = [event.type for event in second.events.for_run(run_id)]
    assert types.count("warning_raised") == 1
    warnings = second.get(run_id).telemetry.warnings
    assert [warning.kind for warning in warnings] == ["repeated_read"]
