"""Adversarial tests for restricted verification process execution."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from sogi.verification.execution import (
    BLOCKED,
    PASSED,
    TIMED_OUT,
    UNAVAILABLE,
    ExecutionPolicy,
)


def _python(program: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"


def test_shell_operators_are_blocked_without_side_effects(tmp_path: Path) -> None:
    marker = tmp_path / "escaped"
    command = f"{_python('print(1)')} && touch {shlex.quote(str(marker))}"

    result = ExecutionPolicy().run(command, cwd=tmp_path, timeout=5)

    assert result.status == BLOCKED
    assert result.success is None
    assert not marker.exists()


def test_sensitive_environment_variables_are_not_inherited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOGI_TEST_SECRET_TOKEN", "must-not-leak")
    command = _python("import os; print(os.environ.get('SOGI_TEST_SECRET_TOKEN', 'filtered'))")

    result = ExecutionPolicy().run(command, cwd=tmp_path, timeout=5)

    assert result.status == PASSED
    assert result.output_tail.strip() == "filtered"


def test_output_capture_is_bounded_to_tail(tmp_path: Path) -> None:
    policy = ExecutionPolicy(max_output_bytes=128)

    result = policy.run(_python("print('x' * 10000)"), cwd=tmp_path, timeout=5)

    assert result.status == PASSED
    assert len(result.output_tail.encode()) <= 128
    assert set(result.output_tail.strip()) == {"x"}


def test_timeout_terminates_check(tmp_path: Path) -> None:
    result = ExecutionPolicy().run(
        _python("import time; time.sleep(10)"),
        cwd=tmp_path,
        timeout=0.05,
    )

    assert result.status == TIMED_OUT
    assert result.success is False
    assert "timed out" in result.output_tail


def test_missing_allowed_tool_is_unavailable(tmp_path: Path) -> None:
    policy = ExecutionPolicy(allowed_executables=frozenset({"sogi-missing-check-tool"}))

    result = policy.run("sogi-missing-check-tool --version", cwd=tmp_path, timeout=5)

    assert result.status == UNAVAILABLE
    assert result.success is None


def test_executable_repository_script_is_allowed(tmp_path: Path) -> None:
    script = tmp_path / "check-project"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    result = ExecutionPolicy().run("./check-project", cwd=tmp_path, timeout=5)

    assert result.status == PASSED
    assert result.success is True


def test_absolute_external_executable_is_blocked(tmp_path: Path) -> None:
    result = ExecutionPolicy().run("/bin/echo unsafe", cwd=tmp_path, timeout=5)

    assert result.status == BLOCKED
    assert result.success is None
