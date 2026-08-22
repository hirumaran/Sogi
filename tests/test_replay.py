"""Tests for deterministic event replay and integrity checking."""

from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.cli import main
from sogi.events.replay import compare_with_snapshot, replay
from sogi.verification.discovery import DiscoveredCheck
from sogi.runs.service import RunService


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _exercise(service: RunService) -> str:
    run_id = service.start(
        "Fix expired refresh token redirect",
        acceptance_criteria=("Expired tokens redirect",),
        constraints=("Preserve OAuth",),
    ).run_id
    service.record_file_read(run_id, "src/auth.py")
    service.record_file_read(run_id, "src/auth.py")
    service.record_file_modified(run_id, "src/auth.py")
    service.record_decision(run_id, "Handle expiration in middleware")
    service.record_failed_approach(run_id, "Patching validate_token directly")
    service.command_started(run_id, "pytest tests/")
    service.command_finished(run_id, "pytest tests/", exit_code=0, success=True)
    service.command_finished(run_id, "make lint", exit_code=1, success=False)
    service.acknowledge(run_id, "scope_expansion", "billing/charge.py")
    service.verify(run_id, checks=(DiscoveredCheck(name="t", command="exit 0", kind="test"),))
    return run_id


def test_replay_reproduces_state_from_events(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = _exercise(service)

    events = service.events.for_run(run_id)
    rebuilt = replay(events)

    stored = service.get(run_id)
    assert rebuilt.state.phase == stored.state.phase == __import__(
        "sogi.core.phases", fromlist=["EngineeringPhase"]
    ).EngineeringPhase.DONE
    assert rebuilt.state.decisions == ["Handle expiration in middleware"]
    assert rebuilt.state.failed_approaches == ["Patching validate_token directly"]
    assert rebuilt.state.files_modified == ["src/auth.py"]
    assert rebuilt.telemetry.files_read == ["src/auth.py", "src/auth.py"]
    assert rebuilt.state.acknowledged.keys() == {"scope_expansion:billing/charge.py"}
    # command attribution mirrors the service's FIFO matching
    pytest_cmd = [c for c in rebuilt.telemetry.commands if c.command == "pytest tests/"][0]
    assert pytest_cmd.started_at != pytest_cmd.finished_at


def test_integrity_ok_after_normal_flow(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = _exercise(service)

    result = compare_with_snapshot(replay(service.events.for_run(run_id)), service.get(run_id))

    assert result["mismatches"] == []
    assert any("context" in item for item in result["snapshot_only"])


def test_integrity_detects_snapshot_tampering(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = _exercise(service)

    tampered = service.get(run_id)
    tampered.state.decisions.append("a decision with no event")
    comparison = compare_with_snapshot(replay(service.events.for_run(run_id)), tampered)

    assert any("decisions" in item for item in comparison["mismatches"])


def test_cli_rebuild_and_integrity(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = _exercise(service)
    service.close()

    code = main(["run", "rebuild", run_id, "--repo", str(repo)])
    assert code == 0

    code = main(["run", "check-integrity", run_id, "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "INTEGRITY: OK" in out
    assert code == 0


def test_cli_rebuild_unknown_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "rebuild", "nope", "--repo", str(repo)])
    assert code == 1
