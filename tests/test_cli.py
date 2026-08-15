import json
from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.cli import main
from sogi.runs.service import RunService


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _start_run(repo: Path, objective: str = "Fix auth") -> str:
    service = RunService(repo, provider=FakeProvider(repo))
    record = service.start(objective, compile_context=False)
    service.close()
    return record.run_id


def test_run_start_text(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    code = main(
        [
            "run",
            "start",
            "Fix expired refresh token redirect",
            "--repo",
            str(repo),
            "--no-context",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Run:" in out
    assert "Objective:" in out
    assert "Fix expired refresh token redirect" in out
    assert "Phase:" in out
    assert "UNDERSTAND" in out
    assert "Context budget:" in out
    assert "not compiled" in out


def test_run_start_json(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    code = main(
        [
            "run",
            "start",
            "Fix auth",
            "--repo",
            str(repo),
            "--criterion",
            "Redirect to /login",
            "--no-context",
            "--format",
            "json",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["objective"] == "Fix auth"
    assert payload["task"]["acceptance_criteria"] == ["Redirect to /login"]
    assert payload["state"]["phase"] == "understand"


def test_run_show(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    run_id = _start_run(repo)
    service = RunService(repo, provider=FakeProvider(repo))
    service.record_decision(run_id, "Use middleware")
    service.close()

    code = main(["run", "show", run_id, "--repo", str(repo)])

    assert code == 0
    out = capsys.readouterr().out
    assert f"SOGI RUN {run_id}" in out
    assert "Fix auth" in out
    assert "1. Use middleware" in out


def test_run_show_unknown_run(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    code = main(["run", "show", "missing", "--repo", str(repo)])

    assert code == 1
    assert "missing" in capsys.readouterr().err


def test_run_events(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    run_id = _start_run(repo)
    service = RunService(repo, provider=FakeProvider(repo))
    service.record_file_read(run_id, "src/auth.py")
    service.close()

    code = main(["run", "events", run_id, "--repo", str(repo)])

    assert code == 0
    out = capsys.readouterr().out
    assert "SOGI EVENT LOG" in out
    assert "task_created" in out
    assert "file_read" in out
    assert "src/auth.py" in out


def test_run_events_json(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    run_id = _start_run(repo)
    code = main(["run", "events", run_id, "--repo", str(repo), "--format", "json"])

    assert code == 0
    events = json.loads(capsys.readouterr().out)
    assert [event["type"] for event in events] == ["task_created"]


def test_run_list(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    _start_run(repo, "Fix auth")
    _start_run(repo, "Add billing")

    code = main(["run", "list", "--repo", str(repo)])

    assert code == 0
    out = capsys.readouterr().out
    assert "SOGI RUNS" in out
    assert "Fix auth" in out
    assert "Add billing" in out


def test_run_list_empty(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    code = main(["run", "list", "--repo", str(repo)])

    assert code == 0
    assert "No runs yet." in capsys.readouterr().out


def test_context_requires_task_or_run(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    code = main(["context", "--repo", str(repo)])

    assert code == 2
    assert "requires a task or --run" in capsys.readouterr().err


def test_context_with_run_uses_run_task(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    run_id = _start_run(repo, "Fix expired refresh token redirect")

    code = main(["context", "--run", run_id, "--repo", str(repo), "--budget", "200"])

    assert code == 0
    out = capsys.readouterr().out
    assert "SOGI CONTEXT" in out
    assert "Fix expired refresh token redirect" in out

    service = RunService(repo, provider=FakeProvider(repo))
    record = service.get(run_id)
    assert record.context is not None
    assert record.context.selected_tokens <= 200
    service.close()


def test_run_start_in_missing_repo(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(["run", "start", "Fix auth", "--repo", str(tmp_path / "missing")])

    assert code == 1
    assert "does not exist" in capsys.readouterr().err
