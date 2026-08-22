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
    service.complete(rid)
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
    assert metrics.commands_executed == 2
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
