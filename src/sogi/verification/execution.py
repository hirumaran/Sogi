"""Restricted process execution for repository verification checks.

This is a secure-by-default *launch policy*, not a container boundary. It
removes shell interpretation, constrains which executables may be launched,
filters inherited environment variables, bounds captured output, and kills a
timed-out process group. Repository tests still execute repository code, so
hostile repositories ultimately require an OS/container sandbox as well.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

PASSED = "passed"
FAILED = "failed"
UNAVAILABLE = "unavailable"
BLOCKED = "blocked"
TIMED_OUT = "timed_out"

_DEFAULT_EXECUTABLES = frozenset(
    {
        "bun",
        "cargo",
        "dotnet",
        "go",
        "gradle",
        "java",
        "make",
        "mvn",
        "mypy",
        "node",
        "npm",
        "npx",
        "pnpm",
        "pytest",
        "ruff",
        "tox",
        "uv",
        "yarn",
    }
)
_ENVIRONMENT_KEYS = frozenset(
    {
        "CI",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "VIRTUAL_ENV",
    }
)
_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&", ">", ">>", "<", "<<"})
_TOOL_NOT_FOUND_CODES = frozenset({126, 127})


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    exit_code: int | None = None
    output_tail: str = ""

    @property
    def success(self) -> bool | None:
        if self.status == PASSED:
            return True
        if self.status in {FAILED, TIMED_OUT}:
            return False
        return None


@dataclass(frozen=True)
class ExecutionPolicy:
    """Policy controlling how a verification command becomes a process."""

    allowed_executables: frozenset[str] = field(default_factory=lambda: _DEFAULT_EXECUTABLES)
    max_output_bytes: int = 64 * 1024
    environment_keys: frozenset[str] = field(default_factory=lambda: _ENVIRONMENT_KEYS)

    def run(self, command: str, *, cwd: Path, timeout: float) -> ExecutionResult:
        root = cwd.expanduser().resolve()
        prepared = self._prepare(command, root)
        if isinstance(prepared, ExecutionResult):
            return prepared
        argv, environment = prepared

        with tempfile.TemporaryFile(mode="w+b") as output:
            try:
                process = subprocess.Popen(  # noqa: S603 - argv is policy-validated
                    argv,
                    cwd=root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except FileNotFoundError:
                return ExecutionResult(
                    UNAVAILABLE,
                    output_tail=f"executable unavailable: {argv[0]}",
                )
            except OSError as exc:
                return ExecutionResult(FAILED, output_tail=f"could not launch check: {exc}")

            timed_out = False
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_group(process)
                returncode = process.wait()

            tail = _read_tail(output, self.max_output_bytes)
            if timed_out:
                return ExecutionResult(
                    TIMED_OUT,
                    exit_code=returncode,
                    output_tail=f"timed out after {timeout}s\n{tail}".rstrip(),
                )
            if returncode in _TOOL_NOT_FOUND_CODES:
                return ExecutionResult(
                    UNAVAILABLE,
                    exit_code=returncode,
                    output_tail=tail or f"exit {returncode}",
                )
            return ExecutionResult(
                PASSED if returncode == 0 else FAILED,
                exit_code=returncode,
                output_tail=tail,
            )

    def _prepare(
        self, command: str, root: Path
    ) -> tuple[list[str], dict[str, str]] | ExecutionResult:
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            return ExecutionResult(BLOCKED, output_tail=f"invalid command syntax: {exc}")
        if not argv:
            return ExecutionResult(BLOCKED, output_tail="empty verification command")
        operator = next((token for token in argv if token in _SHELL_OPERATORS), None)
        if operator:
            return ExecutionResult(
                BLOCKED,
                output_tail=f"shell operator {operator!r} is not allowed by verification policy",
            )

        executable = argv[0]
        basename = Path(executable).name
        allowed_name = basename in self.allowed_executables or basename.startswith("python")
        launch_path = _resolve_executable(executable, root)
        inside_repository = launch_path is not None and _is_relative_to(launch_path.resolve(), root)
        if not allowed_name and not inside_repository:
            return ExecutionResult(
                BLOCKED,
                output_tail=f"executable {executable!r} is not allowed by verification policy",
            )
        if launch_path is None:
            return ExecutionResult(
                UNAVAILABLE,
                output_tail=f"executable unavailable: {executable}",
            )
        # Preserve virtual-environment and shim paths for launch. Resolving a
        # symlink such as .venv/bin/python to its base interpreter would lose
        # that environment's installed packages.
        argv[0] = str(launch_path)
        return argv, _sanitized_environment(self.environment_keys)


def _resolve_executable(executable: str, root: Path) -> Path | None:
    if "/" in executable or "\\" in executable:
        candidate = Path(executable)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve(strict=True)
        except OSError:
            return None
        return candidate.absolute() if candidate.is_file() else None
    found = shutil.which(executable)
    return Path(found).absolute() if found else None


def _sanitized_environment(keys: frozenset[str]) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key in keys}
    environment.update({key: value for key, value in os.environ.items() if key.startswith("LC_")})
    environment["SOGI_VERIFICATION"] = "1"
    return environment


def _read_tail(output: BinaryIO, limit: int) -> str:
    output.flush()
    output.seek(0, os.SEEK_END)
    size = output.tell()
    output.seek(max(0, size - max(1, limit)))
    return output.read().decode("utf-8", errors="replace")


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows is not in the current CI matrix
            process.kill()
    except ProcessLookupError:
        pass


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
