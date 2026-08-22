"""Trial execution for controlled Sogi-on vs Sogi-off experiments.

The harness enforces the experiment discipline: identical task, repository,
model, and limits across arms; only the presence of Sogi supervision differs.
Results are raw JSONL — rendered reports are derived views, never the record
of truth.

The harness does not fabricate measurements: token counts and costs appear in
results only when the agent runner actually reported them (provenance field).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .task import EvalTask


class ExperimentArm(str, Enum):
    BASELINE = "baseline"
    SOGI = "sogi"


@dataclass
class TrialResult:
    """One executed trial. Written verbatim to JSONL."""

    trial_id: str
    task_id: str
    arm: str
    agent: str
    started_at: float
    duration_seconds: float
    exit_code: int | None
    success: bool  # agent process exited successfully; not a correctness claim
    verification_outcome: str | None = None
    verification_report: dict[str, Any] | None = None
    verified_success: bool | None = None
    run_id: str | None = None
    grader_run_id: str | None = None
    base_commit: str | None = None
    patch_fingerprint: str | None = None
    patch_assessment: dict[str, Any] | None = None
    workspace_isolated: bool = False
    output_artifact: str | None = None
    patch_artifact: str | None = None
    input_tokens: int | None = None  # None = not reported by the host
    output_tokens: int | None = None
    cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


AgentRunner = Callable[[EvalTask, ExperimentArm], "RunnerOutcome"]


@dataclass
class RunnerOutcome:
    exit_code: int | None
    output: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)


class EvalWorkspaceError(RuntimeError):
    """Raised when a controlled trial cannot create its immutable workspace."""


def mock_runner(outcome_exit: int = 0) -> AgentRunner:
    """A deterministic runner for dry-runs and tests; performs no real work."""

    def run(task: EvalTask, arm: ExperimentArm) -> RunnerOutcome:
        return RunnerOutcome(
            exit_code=outcome_exit,
            output=f"mock execution of {task.task_id} under {arm.value}",
            notes=["mock"],
        )

    return run


class MockRunner:
    """Callable form of :func:`mock_runner` (reports no usage by design)."""

    def __init__(self, outcome_exit: int = 0) -> None:
        self.outcome_exit = outcome_exit

    def __call__(self, task: EvalTask, arm: ExperimentArm) -> RunnerOutcome:
        return mock_runner(self.outcome_exit)(task, arm)


class ShellAgentRunner:
    """Executes an agent CLI via a command template.

    Template placeholders: ``{prompt}``, ``{repo}``. The command is invoked
    without a shell when possible (shlex split on POSIX). Token/cost fields
    are parsed only if the agent emits a final JSON line containing them;
    otherwise they stay unreported (None) rather than estimated.
    """

    def __init__(self, template: str, *, timeout: float = 1800.0, shell: bool = False) -> None:
        self.template = template
        self.timeout = timeout
        self.shell = shell

    def __call__(self, task: EvalTask, arm: ExperimentArm) -> RunnerOutcome:
        repo_root = Path(task.repo)
        command = self.template.format(
            prompt=task.prompt.replace('"', '\\"'), repo=str(repo_root), arm=arm.value
        )
        argv = command if self.shell else shlex.split(command)
        try:
            completed = subprocess.run(
                argv,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=self.shell,
            )
        except subprocess.TimeoutExpired:
            return RunnerOutcome(
                exit_code=None, output="", notes=[f"timeout after {self.timeout}s"]
            )
        except OSError as exc:
            return RunnerOutcome(exit_code=None, output="", notes=[str(exc)])

        usage = _extract_usage(completed.stdout or "")
        return RunnerOutcome(
            exit_code=completed.returncode,
            output=(completed.stdout or "")[-2000:],
            **usage,
        )


def _extract_usage(output: str) -> dict[str, Any]:
    """Pull host-reported usage from the last JSON object in agent output."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {
                "input_tokens": _int_or_none(
                    data.get("input_tokens") or data.get("usage", {}).get("input_tokens")
                ),
                "output_tokens": _int_or_none(
                    data.get("output_tokens") or data.get("usage", {}).get("output_tokens")
                ),
                "cost_usd": _float_or_none(data.get("cost_usd") or data.get("total_cost_usd")),
            }
    return {}


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def run_suite(
    tasks: list[EvalTask],
    *,
    arm: ExperimentArm,
    runner: AgentRunner,
    agent_label: str = "agent",
    sogi_repo: Path | None = None,
    repeats: int = 1,
    isolate: bool = True,
    artifacts_dir: Path | None = None,
) -> list[TrialResult]:
    """Run every task (with repeats) under one arm and collect raw results.

    Controlled trials clone the task repository at ``base_commit`` for every
    repetition. This prevents one arm or repeat from inheriting another's
    edits. ``isolate=False`` exists only for mock/dry-run compatibility and is
    recorded in every result so uncontrolled observations cannot be mistaken
    for benchmark evidence.
    """
    results: list[TrialResult] = []
    for task in tasks:
        for repeat in range(repeats):
            trial_id = uuid.uuid4().hex[:10]
            full_trial_id = f"{trial_id}-{repeat}"
            if isolate:
                with isolated_trial(task) as (trial_task, resolved_commit):
                    results.append(
                        _run_trial(
                            trial_task,
                            arm=arm,
                            runner=runner,
                            agent_label=agent_label,
                            trial_id=full_trial_id,
                            resolved_commit=resolved_commit,
                            isolated=True,
                            artifacts_dir=artifacts_dir,
                            supervise=arm is ExperimentArm.SOGI,
                            grade=True,
                        )
                    )
            else:
                result = _run_trial(
                    task,
                    arm=arm,
                    runner=runner,
                    agent_label=agent_label,
                    trial_id=full_trial_id,
                    resolved_commit=task.base_commit,
                    isolated=False,
                    artifacts_dir=artifacts_dir,
                    grade_repo=sogi_repo if arm is ExperimentArm.SOGI else None,
                    supervise=False,
                    grade=sogi_repo is not None and arm is ExperimentArm.SOGI,
                )
                result.notes.append("uncontrolled workspace: isolation disabled")
                results.append(result)
    return results


def _run_trial(
    task: EvalTask,
    *,
    arm: ExperimentArm,
    runner: AgentRunner,
    agent_label: str,
    trial_id: str,
    resolved_commit: str | None,
    isolated: bool,
    artifacts_dir: Path | None,
    grade_repo: Path | None = None,
    supervise: bool,
    grade: bool,
) -> TrialResult:
    repo_root = Path(task.repo).expanduser().resolve()
    started = time.time()
    supervised_run_id = _start_supervision(repo_root, task) if supervise else None
    outcome = runner(task, arm)
    result = TrialResult(
        trial_id=trial_id,
        task_id=task.task_id,
        arm=arm.value,
        agent=agent_label,
        started_at=started,
        duration_seconds=round(time.time() - started, 3),
        exit_code=outcome.exit_code,
        success=outcome.exit_code == 0,
        run_id=supervised_run_id,
        base_commit=resolved_commit,
        workspace_isolated=isolated,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cost_usd=outcome.cost_usd,
        notes=list(outcome.notes),
    )
    result.patch_fingerprint = _patch_fingerprint(repo_root)
    _write_artifacts(result, repo_root, outcome.output, artifacts_dir)

    # Both arms are graded after execution by a fresh Sogi run. The treatment
    # run is deliberately not reused: supervision and evaluation must remain
    # separate roles to avoid grading Sogi with Sogi's own mutable state.
    if grade:
        grading_root = grade_repo.expanduser().resolve() if grade_repo else repo_root
        result.verification_outcome = _grade(grading_root, result, task)
        result.verified_success = (
            result.verification_outcome == "PASS"
            if result.verification_outcome is not None
            else None
        )
    return result


def _start_supervision(repo_root: Path, task: EvalTask) -> str:
    try:
        from sogi.runs.service import RunService

        with RunService(repo_root) as service:
            return service.start(
                task.prompt,
                acceptance_criteria=task.acceptance_criteria,
                constraints=task.constraints,
                compile_context=False,
            ).run_id
    except Exception as exc:  # noqa: BLE001 - normalize integration failures for the harness
        raise EvalWorkspaceError(f"Could not initialize Sogi treatment: {exc}") from exc


def _grade(repo_root: Path, result: TrialResult, task: EvalTask) -> str | None:
    try:
        from sogi.runs.service import RunService

        with RunService(repo_root) as service:
            grader = service.start(
                task.prompt,
                acceptance_criteria=task.acceptance_criteria,
                constraints=task.constraints,
                compile_context=False,
            )
            result.grader_run_id = grader.run_id
            report = service.verify(grader.run_id)
            result.verification_report = report.to_dict()
            result.patch_assessment = service.get(grader.run_id).telemetry.patch_assessment
            return report.outcome
    except Exception as exc:  # noqa: BLE001 - harness must survive verifier failures
        result.notes.append(f"verification failed: {exc}")
        return None


@contextmanager
def isolated_trial(task: EvalTask) -> Iterator[tuple[EvalTask, str]]:
    """Yield a disposable clone pinned to the task's exact base revision."""
    if not task.base_commit:
        raise EvalWorkspaceError(
            f"Task {task.task_id!r} has no base_commit; controlled trials require one"
        )
    source_path = Path(task.repo).expanduser()
    source = str(source_path.resolve()) if source_path.exists() else task.repo
    with tempfile.TemporaryDirectory(prefix=f"sogi-eval-{task.task_id}-") as temporary:
        workspace = Path(temporary) / "repo"
        _git_checked(
            Path(temporary),
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            source,
            str(workspace),
        )
        _git_checked(workspace, "checkout", "--quiet", "--detach", task.base_commit)
        resolved = _git_checked(workspace, "rev-parse", "HEAD").strip()
        yield replace(task, repo=str(workspace), base_commit=resolved), resolved


def _git_checked(cwd: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvalWorkspaceError(f"Git failed while preparing trial: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise EvalWorkspaceError(f"Git {' '.join(args[:2])} failed: {detail}")
    return completed.stdout


def _patch_fingerprint(repo_root: Path) -> str | None:
    from sogi.repository.worktree import capture_fingerprint

    return capture_fingerprint(repo_root).diff_hash


def _write_artifacts(
    result: TrialResult,
    repo_root: Path,
    output: str,
    artifacts_dir: Path | None,
) -> None:
    if artifacts_dir is None:
        return
    target = artifacts_dir.expanduser().resolve() / result.trial_id
    target.mkdir(parents=True, exist_ok=True)
    output_path = target / "agent-output.txt"
    output_path.write_text(output, encoding="utf-8")
    result.output_artifact = str(output_path)

    tracked = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    pieces = [tracked.stdout] if tracked.returncode == 0 else []
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if untracked.returncode == 0:
        from sogi.repository.worktree import filter_transient

        for relative in filter_transient(untracked.stdout.split("\0")):
            addition = subprocess.run(
                ["git", "diff", "--binary", "--no-index", "--", "/dev/null", relative],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            # git diff --no-index returns 1 when differences were found.
            if addition.returncode in {0, 1}:
                pieces.append(addition.stdout)
    patch_path = target / "changes.patch"
    patch_path.write_text("".join(pieces), encoding="utf-8")
    result.patch_artifact = str(patch_path)
