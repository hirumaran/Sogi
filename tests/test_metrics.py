"""Tests for run metrics."""

from pathlib import Path

import pytest

from sogi.cli import main
from sogi.runs.service import RunService
from sogi.telemetry.metrics import RunMetrics


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _exercise_run(repo: Path) -> str:
    from sogi.verification.discovery import DiscoveredCheck

    service = RunService(repo)
    run = service.start("Fix auth", acceptance_criteria=("Auth works",))
    rid = run.run_id
    for _ in range(4):
        service.record_file_read(rid, "src/auth.py")
    service.record_file_read(rid, "src/other.py")
    service.record_file_modified(rid, "src/auth.py")
    service.command_started(rid, "pytest")
    service.command_finished(rid, "pytest", exit_code=0, success=True)
    service.command_started(rid, "mypy")
    service.command_finished(rid, "mypy", exit_code=1, success=False)
    service.raise_warning(rid, "scope_expansion", "billing touched")
    passing = DiscoveredCheck(name="t", command="exit 0", kind="test")
    service.verify(rid, checks=(passing,))
    service.complete(rid, allow_unverified=True)
    service.close()
    return rid


def test_metrics_counts_are_accurate(repo: Path) -> None:
    rid = _exercise_run(repo)
    service = RunService(repo)
    metrics = RunMetrics.from_record(service.get(rid))

    assert metrics.run_id == rid
    assert metrics.files_read == 5
    assert metrics.unique_files_read == 2
    assert metrics.repeat_reads == 3
    assert metrics.files_modified == 1
    # Verification commands count as executed commands: they ran on the host.
    assert metrics.commands_executed == 3
    assert metrics.failed_commands == 1
    assert metrics.warnings == {"scope_expansion": 1, "repeated_read": 1}
    assert metrics.interventions == 2
    assert metrics.context_compilations == 1
    assert metrics.last_context_tokens is not None
    assert metrics.phase == "done"
    assert metrics.duration_seconds is not None


def test_metrics_render_and_dict_agree(repo: Path) -> None:
    rid = _exercise_run(repo)
    service = RunService(repo)
    metrics = RunMetrics.from_record(service.get(rid))

    text = metrics.render()
    assert f"METRICS run {rid}" in text
    assert "repeat 3" in text

    payload = metrics.to_dict()
    assert payload["exploration"]["repeat_reads"] == 3


def test_cli_metrics_json(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    rid = _exercise_run(repo)

    code = main(["metrics", rid, "--repo", str(repo), "--format", "json"])

    assert code == 0
    payload = capsys.readouterr().out
    assert '"repeat_reads": 3' in payload


def test_cli_metrics_unknown_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["metrics", "missing", "--repo", str(repo)])
    assert code == 1


def test_usage_metrics_recorded_and_reported(repo: Path) -> None:
    service = RunService(repo)
    rid = service.start("Fix auth", compile_context=False).run_id
    service.record_usage(
        rid,
        agent_host="claude-code",
        model="test-model",
        input_tokens=1500,
        output_tokens=300,
        cached_tokens=200,
        cost_usd=0.012,
    )
    service.record_usage(rid, input_tokens=500, output_tokens=100, cost_usd=0.004)
    service.close()

    service = RunService(repo)
    metrics = RunMetrics.from_record(service.get(rid))

    usage = metrics.usage
    assert usage["provenance"] == "host-reported"
    assert usage["input_tokens"] == 2000  # accumulates across calls
    assert usage["output_tokens"] == 400
    assert usage["model"] == "test-model"
    assert abs(usage["cost_usd"] - 0.016) < 1e-9

    text = metrics.render()
    assert "in=2000" in text
