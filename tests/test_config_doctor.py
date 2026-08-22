"""Tests for .sogi.toml configuration and the doctor command."""

import json
from pathlib import Path

import pytest
from fakes import exit_command

from sogi.cli import main
from sogi.config import SogiConfig
from sogi.runs.service import RunService


def test_missing_config_yields_defaults(tmp_path: Path) -> None:
    config = SogiConfig.load(tmp_path)
    assert config.context_budget is None
    assert config.verification_commands == ()
    assert config.block_on_unverified is True


def test_config_parses_all_sections(tmp_path: Path) -> None:
    (tmp_path / ".sogi.toml").write_text(
        """
[context]
budget = 2500

[verification]
commands = ["pytest -q", "ruff check src"]

[completion]
block_on_unverified = false

[observation]
host = "claude-code"
"""
    )

    config = SogiConfig.load(tmp_path)

    assert config.context_budget == 2500
    assert config.verification_commands == ("pytest -q", "ruff check src")
    assert config.block_on_unverified is False
    assert config.host == "claude-code"


def test_config_applies_to_run_budget(tmp_path: Path) -> None:
    (tmp_path / ".sogi.toml").write_text("[context]\nbudget = 1234\n")

    service = RunService(tmp_path)
    record = service.start("Fix auth", compile_context=False)

    assert record.telemetry.context_budget == 1234


def test_config_verification_commands_are_executed(tmp_path: Path) -> None:
    command = exit_command(0)
    (tmp_path / ".sogi.toml").write_text(f"[verification]\ncommands = [{json.dumps(command)}]\n")

    service = RunService(tmp_path)
    run_id = service.start("Fix auth", compile_context=False).run_id

    report = service.verify(run_id)

    assert report.outcome == "PASS"
    executed = [command.command for command in service.get(run_id).telemetry.commands]
    assert command in executed


def test_cli_doctor_reports_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".git").mkdir()
    code = main(["doctor", "--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert "database" in out
    assert "SOGI DEPENDENCY CHECK" in out
    assert code in {0, 1}  # optional tooling may be absent; doctor reports honestly


def test_cli_doctor_missing_analyzer_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    import sogi.repository.tree_sitter_provider as tsp

    monkeypatch.setattr(tsp, "_detect_command", lambda: ("definitely-not-a-real-binary",))
    (tmp_path / ".git").mkdir()
    code = main(["doctor", "--repo", str(tmp_path)])
    assert "tree-sitter-analyzer" in capsys.readouterr().out
    assert code == 1


def test_doctor_report_flags_only_required_failures(tmp_path: Path) -> None:
    from sogi.doctor import OPTIONAL, REQUIRED, CheckResult, DoctorReport, run_doctor

    report = DoctorReport(
        repo_root=tmp_path,
        checks=[
            CheckResult("git", REQUIRED, False, "missing"),
            CheckResult("ast-grep", OPTIONAL, False, "missing"),
        ],
    )
    assert not report.ok
    assert report.failed_required == ["git"]
    rendered = report.render()
    assert "[FAIL]" in rendered and "ast-grep" in rendered

    # The real collector never marks an optional check as required.
    collected = run_doctor(None)
    names = {check.name for check in collected.checks if check.category == REQUIRED}
    assert {"python", "sogi", "git", "tree-sitter-analyzer"} <= names


def test_doctor_detects_revision_drift(monkeypatch) -> None:
    import json

    import sogi.doctor as doctor_module

    external = doctor_module._EXTERNAL_DIR
    revisions_file = external / "revisions.json"
    if not revisions_file.is_file():
        import pytest

        pytest.skip("external/revisions.json not present")
    pinned = json.loads(revisions_file.read_text(encoding="utf-8"))
    first_name = sorted(pinned)[0]
    pinned[first_name] = "0" * 40

    class _StubRevisionsFile:
        def read_text(self, *_args, **_kwargs) -> str:
            return json.dumps(pinned)

    monkeypatch.setattr(doctor_module, "_REVISIONS_FILE", _StubRevisionsFile())
    result = doctor_module._revision_drift_check()
    assert not result.ok
    assert first_name in result.detail
