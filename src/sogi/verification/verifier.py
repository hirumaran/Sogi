"""Independent verification engine.

Runs the repository's own declared checks (discovered via
:mod:`sogi.verification.discovery`), then maps the observable outcomes back to
the run's acceptance criteria. The agent's claim of completion carries no
weight here — only executed commands and their exit codes do.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sogi.core.run_record import RunRecord

from .discovery import DiscoveredCheck, discover_checks
from .evidence import CriterionResult, map_criteria

#: Truncate captured output so reports stay readable and storage stays small.
_OUTPUT_TAIL_CHARS = 2000

#: Shell-level codes meaning the command could not be launched at all.
_TOOL_NOT_FOUND_CODES = frozenset({126, 127})


@dataclass(frozen=True)
class CheckResult:
    """The outcome of executing one discovered check."""

    check: DiscoveredCheck
    success: bool | None  # None = skipped / not executed
    exit_code: int | None = None
    output_tail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.check.name,
            "command": self.check.command,
            "kind": self.check.kind,
            "success": self.success,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
        }


@dataclass
class VerificationReport:
    """Full verification outcome for one run."""

    run_id: str
    checks: tuple[CheckResult, ...] = ()
    criteria: tuple[CriterionResult, ...] = ()
    outcome: str = "FAIL"
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.outcome == "PASS"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "outcome": self.outcome,
            "checks": [result.to_dict() for result in self.checks],
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        lines = [f"VERIFICATION {self.outcome}", f"Run: {self.run_id}", ""]
        lines.append("CHECKS")
        if not self.checks:
            lines.append("  No repository checks discovered.")
        for result in self.checks:
            mark = _mark(result.success)
            suffix = "" if result.exit_code is None else f" (exit {result.exit_code})"
            lines.append(f"  [{mark}] {result.check.name}: {result.check.command}{suffix}")
        lines.append("")
        lines.append("ACCEPTANCE CRITERIA")
        if not self.criteria:
            lines.append("  No acceptance criteria defined for this run.")
        for criterion in self.criteria:
            mark = _mark(
                True
                if criterion.status == "SATISFIED"
                else False
                if criterion.status == "VIOLATED"
                else None
            )
            lines.append(f"  [{mark}] {criterion.status}: {criterion.criterion}")
            for item in criterion.evidence:
                lines.append(f"        evidence: {item}")
            if criterion.note:
                lines.append(f"        note: {criterion.note}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _mark(success: bool | None) -> str:
    return "x" if success else "!" if success is False else "?"


class Verifier:
    """Executes discovered checks and produces a VerificationReport."""

    def __init__(self, repo_root: Path, *, timeout: float = 600.0) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.timeout = timeout

    def verify(
        self,
        record: RunRecord,
        *,
        checks: tuple[DiscoveredCheck, ...] | None = None,
    ) -> VerificationReport:
        discovered = checks if checks is not None else discover_checks(self.repo_root)
        results = tuple(self._run(check) for check in discovered)
        criteria = map_criteria(record, results)
        notes: list[str] = []

        failed = [result for result in results if result.success is False]
        executable = [result for result in results if result.success is not None]
        if not results or not executable:
            outcome = "INCONCLUSIVE"
            notes = ["No repository checks could be executed; nothing was verified."]
        elif failed:
            outcome = "FAIL"
        else:
            violated = [item for item in criteria if item.status == "VIOLATED"]
            unverified = [item for item in criteria if item.status == "UNVERIFIED"]
            if violated:
                outcome = "FAIL"
            elif unverified:
                outcome = "PASS_WITH_UNVERIFIED"
                notes = [
                    f"{len(unverified)} criterion/criteria could not be verified "
                    "from available evidence."
                ]
            else:
                outcome = "PASS"

        return VerificationReport(
            run_id=record.run_id,
            checks=results,
            criteria=criteria,
            outcome=outcome,
            notes=notes,
        )

    def _run(self, check: DiscoveredCheck) -> CheckResult:
        try:
            completed = subprocess.run(  # noqa: S602 - repo-declared command
                check.command,
                shell=True,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                check=check, success=False, output_tail=f"timed out after {self.timeout}s"
            )
        except OSError as exc:
            return CheckResult(check=check, success=False, output_tail=str(exc))
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode in _TOOL_NOT_FOUND_CODES:
            # The tool itself is not installed: that is an environment fact,
            # not evidence that the code fails its requirements.
            tail = output[-_OUTPUT_TAIL_CHARS:] if output else f"exit {completed.returncode}"
            return CheckResult(
                check=check,
                success=None,
                exit_code=completed.returncode,
                output_tail=tail[:200],
            )
        return CheckResult(
            check=check,
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            output_tail=output[-_OUTPUT_TAIL_CHARS:],
        )
