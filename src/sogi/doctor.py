"""External dependency doctor.

Sogi sits between a coding agent and several external programs (Tree-sitter
Analyzer, ast-grep, Semgrep, ...). When something breaks, the first question is
always *which layer* broke. The doctor answers that before work starts: it
verifies each dependency exists, executes it, reports its version, and checks
the pinned external revisions for drift.

Checks are categorized:

- ``required``   Sogi's core path fails without these (python, git, analyzer).
- ``optional``   Feature-gated integrations; missing only disables features.
- ``research``   Benchmark/experiment infrastructure that never blocks runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED = "required"
OPTIONAL = "optional"
RESEARCH = "research"

_EXTERNAL_DIR = Path(__file__).resolve().parents[2] / "external"
_REVISIONS_FILE = _EXTERNAL_DIR / "revisions.json"


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: str
    ok: bool
    detail: str

    def render(self) -> str:
        mark = "ok  " if self.ok else ("--  " if self.category != REQUIRED else "FAIL")
        return f"  [{mark}] {self.name:<24} {self.detail}"


@dataclass
class DoctorReport:
    repo_root: Path | None = None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failed_required(self) -> list[str]:
        return [check.name for check in self.checks if not check.ok and check.category == REQUIRED]

    @property
    def ok(self) -> bool:
        return not self.failed_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "repo": str(self.repo_root) if self.repo_root else None,
            "failed_required": self.failed_required,
            "checks": [
                {
                    "name": check.name,
                    "category": check.category,
                    "ok": check.ok,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }

    def render(self) -> str:
        lines = ["SOGI DEPENDENCY CHECK", "=====================", ""]
        lines.extend(check.render() for check in self.checks)
        if self.ok:
            failed_optional = [
                check.name for check in self.checks if not check.ok and check.category == OPTIONAL
            ]
            tail = "All required checks passed."
            if failed_optional:
                tail += f" Optional but absent: {', '.join(failed_optional)}."
            lines.extend(("", tail))
        else:
            lines.extend(("", f"Problems found: {', '.join(self.failed_required)}"))
        return "\n".join(lines)


def _probe_command(names: tuple[str, ...], *arguments: str) -> tuple[bool, str]:
    """Locate a CLI by any of its names and run a minimal version invocation."""
    executable = next((shutil.which(name) for name in names if shutil.which(name)), None)
    if executable is None:
        return False, "not found on PATH"
    try:
        completed = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{Path(executable).name} found but failed to execute ({exc})"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0].strip() if output else ""
    if completed.returncode != 0:
        return False, f"{Path(executable).name} exited {completed.returncode}"
    return True, version or Path(executable)


def _execute(command: tuple[str, ...], *arguments: str) -> tuple[bool, str]:
    """Run an already-resolved command with a minimal invocation."""
    try:
        completed = subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"failed to execute ({exc})"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0].strip() if output else ""
    if completed.returncode != 0:
        return False, f"exited {completed.returncode}"
    return True, version


def _python_package_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _analyzer_check() -> CheckResult:
    # The provider resolves SOGI_TSA_COMMAND / PATH / workspace checkout /
    # uvx in one place; reuse it so doctor sees exactly what runs will use.
    from sogi.repository.tree_sitter_provider import _detect_command

    command = _detect_command()
    label = " ".join(command)
    ok, version = _execute(command, "--version")
    if not ok:
        # Some CLIs lack --version; executing --help still proves the binary runs.
        runs_at_all, _ = _execute(command, "--help")
        if runs_at_all:
            return CheckResult("tree-sitter-analyzer", REQUIRED, True, f"{label} (executable)")
        hint = "pip install 'sogi[analyzer]'"
        if command[0] == "uvx":
            hint = "uv tool install tree-sitter-analyzer (or pip install 'sogi[analyzer]')"
        return CheckResult("tree-sitter-analyzer", REQUIRED, False, f"missing ({hint})")
    return CheckResult("tree-sitter-analyzer", REQUIRED, True, f"{label} {version}".strip())


def _revision_drift_check() -> CheckResult:
    """Compare pinned external revisions against the actual local checkouts."""
    if not _EXTERNAL_DIR.is_dir():
        return CheckResult("external-revisions", RESEARCH, True, "no external/ checkouts")
    try:
        pinned: dict[str, str] = json.loads(_REVISIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CheckResult(
            "external-revisions",
            RESEARCH,
            True,
            "external/revisions.json missing (run scripts/pin-revisions.py)",
        )
    drift: list[str] = []
    for checkout in sorted(_EXTERNAL_DIR.iterdir()):
        if not checkout.is_dir():
            continue
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=checkout,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        sha = completed.stdout.strip() if completed.returncode == 0 else ""
        expected = pinned.get(checkout.name)
        if sha and expected and sha != expected:
            drift.append(checkout.name)
    if drift:
        return CheckResult(
            "external-revisions",
            RESEARCH,
            False,
            f"checkout(s) moved past pin: {', '.join(drift)} (re-run scripts/pin-revisions.py)",
        )
    return CheckResult(
        "external-revisions", RESEARCH, True, f"{len(pinned)} pinned revision(s) current"
    )


def collect_checks(repo_root: Path | None) -> list[CheckResult]:
    """Run every environment check. Slow probes are kept bounded and independent."""
    checks: list[CheckResult] = []

    version = sys.version.split()[0]
    checks.append(
        CheckResult("python", REQUIRED, sys.version_info >= (3, 10), version),
    )

    from sogi import __version__

    checks.append(CheckResult("sogi", REQUIRED, True, __version__))

    git_ok, git_detail = _probe_command(("git",), "--version")
    checks.append(CheckResult("git", REQUIRED, git_ok, git_detail))

    checks.append(_analyzer_check())

    mcp_version = _python_package_version("mcp")
    checks.append(
        CheckResult(
            "mcp-sdk",
            OPTIONAL,
            mcp_version is not None,
            mcp_version or "missing (pip install 'sogi[mcp]')",
        )
    )

    ast_grep_ok, ast_grep_detail = _probe_command(("ast-grep", "sg"), "--version")
    checks.append(
        CheckResult(
            "ast-grep",
            OPTIONAL,
            ast_grep_ok,
            ast_grep_detail
            if ast_grep_ok
            else "missing (patch proposal disabled; brew install ast-grep)",
        )
    )

    semgrep_ok, semgrep_detail = _probe_command(("semgrep",), "--version")
    checks.append(
        CheckResult(
            "semgrep",
            OPTIONAL,
            semgrep_ok,
            semgrep_detail if semgrep_ok else "missing (static-analysis evidence disabled)",
        )
    )

    comby_ok, comby_detail = _probe_command(("comby",), "-version")
    checks.append(CheckResult("comby", OPTIONAL, comby_ok, comby_detail))

    docker_ok, docker_detail = _probe_command(
        ("docker",), "version", "--format", "{{.Server.Version}}"
    )
    checks.append(
        CheckResult(
            "docker",
            RESEARCH,
            docker_ok,
            docker_detail if docker_ok else "missing (benchmark grading unavailable)",
        )
    )

    checks.append(_revision_drift_check())

    if repo_root is not None:
        repo_ok = repo_root.is_dir()
        checks.append(CheckResult("repo", REQUIRED, repo_ok, str(repo_root)))
        if repo_ok:
            checks.append(
                CheckResult("git-repo", REQUIRED, (repo_root / ".git").exists(), "worktree")
            )
            checks.extend(_repo_checks(repo_root))

    return checks


def _repo_checks(repo_root: Path) -> list[CheckResult]:
    """Repository-level checks; failures here degrade features, not the install."""
    results: list[CheckResult] = []
    try:
        from sogi.storage.db import SogiDatabase
        from sogi.verification.discovery import discover_checks

        database = SogiDatabase(repo_root / ".sogi")
        try:
            schema = database.schema_version()
            results.append(CheckResult("database", OPTIONAL, schema >= 1, f"schema v{schema}"))
        finally:
            database.close()

        active = (repo_root / ".sogi" / "active_run").is_file()
        results.append(
            CheckResult("active-run", RESEARCH, True, "present" if active else "none")
        )
        discovered = discover_checks(repo_root)
        results.append(
            CheckResult("verification-checks", RESEARCH, True, f"{len(discovered)} discovered")
        )
    except (OSError, ValueError, RuntimeError):
        results.append(CheckResult("database", OPTIONAL, False, "could not open .sogi store"))
    return results


def run_doctor(repo_root: Path | None = None) -> DoctorReport:
    resolved = repo_root.expanduser().resolve() if repo_root else None
    return DoctorReport(repo_root=resolved, checks=collect_checks(resolved))
