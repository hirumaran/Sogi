"""Tests for .sogi.toml configuration and the doctor command."""

from pathlib import Path

import pytest

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
    (tmp_path / ".sogi.toml").write_text('[verification]\ncommands = ["exit 0"]\n')

    service = RunService(tmp_path)
    run_id = service.start("Fix auth", compile_context=False).run_id

    report = service.verify(run_id)

    assert report.outcome == "PASS"
    executed = [command.command for command in service.get(run_id).telemetry.commands]
    assert "exit 0" in executed


def test_cli_doctor_reports_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".git").mkdir()
    code = main(["doctor", "--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert "database" in out
    assert code in {0, 1}  # analyzer may be absent; doctor reports honestly


def test_cli_doctor_missing_analyzer_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def no_analyzer(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "tree_sitter_analyzer":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_analyzer)
    (tmp_path / ".git").mkdir()
    code = main(["doctor", "--repo", str(tmp_path)])
    assert "tree-sitter-analyzer" in capsys.readouterr().out
    assert code == 1
