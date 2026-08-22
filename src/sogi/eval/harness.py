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
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
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
    success: bool  # runner-level success; verification outcome recorded separately
    verification_outcome: str | None = None
    run_id: str | None = None
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
) -> list[TrialResult]:
    """Run every task (with repeats) under one arm and collect raw results."""
    results: list[TrialResult] = []
    for task in tasks:
        for repeat in range(repeats):
            trial_id = uuid.uuid4().hex[:10]
            started = time.time()
            outcome = runner(task, arm)
            result = TrialResult(
                trial_id=f"{trial_id}-{repeat}",
                task_id=task.task_id,
                arm=arm.value,
                agent=agent_label,
                started_at=started,
                duration_seconds=round(time.time() - started, 3),
                exit_code=outcome.exit_code,
                success=outcome.exit_code == 0,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=outcome.cost_usd,
                notes=list(outcome.notes),
            )
            # Under the Sogi arm, independent verification grades completion.
            if arm is ExperimentArm.SOGI and sogi_repo is not None:
                result.verification_outcome = _verify_latest_run(sogi_repo, result, task)
            results.append(result)
    return results


def _verify_latest_run(sogi_repo: Path, result: TrialResult, task: EvalTask) -> str | None:
    try:
        from sogi.runs.service import RunService

        with RunService(sogi_repo) as service:
            run_id = service.active_run_id()
            if run_id is None:
                result.notes.append("no active sogi run found for verification")
                return None
            report = service.verify(run_id, checks=())
            result.run_id = run_id
            return report.outcome
    except Exception as exc:  # noqa: BLE001 - harness must survive verifier failures
        result.notes.append(f"verification failed: {exc}")
        return None
