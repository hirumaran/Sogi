"""Tests for the evaluation harness."""

import json
import subprocess
from pathlib import Path

import pytest

from sogi.cli import main
from sogi.eval import ExperimentArm, MockRunner, run_suite
from sogi.eval.compare import compare, render, summarize
from sogi.eval.harness import EvalWorkspaceError, RunnerOutcome
from sogi.eval.task import EvalTask, load_suite


@pytest.fixture()
def suite(tmp_path: Path) -> Path:
    path = tmp_path / "suite.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "t1",
                        "repo": str(tmp_path / "repo1"),
                        "base_commit": "abc123",
                        "prompt": "Fix the auth redirect",
                        "acceptance_criteria": ["redirects to /login"],
                    },
                    {"task_id": "t2", "repo": str(tmp_path / "repo2"), "prompt": "Add tests"},
                ]
            }
        )
    )
    return path


@pytest.fixture()
def eval_git_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "eval-repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "sogi@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Sogi Eval"], cwd=root, check=True)
    (root / "app.py").write_text("VALUE = 1\n")
    (root / "Makefile").write_text("test:\n\t@true\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return root, head


def test_load_suite_validates(suite: Path) -> None:
    tasks = load_suite(suite)
    assert [task.task_id for task in tasks] == ["t1", "t2"]
    assert tasks[0].acceptance_criteria == ("redirects to /login",)

    empty = suite.parent / "empty.json"
    empty.write_text('{"tasks": []}')
    with pytest.raises(ValueError):
        load_suite(empty)


def test_run_suite_mock_produces_raw_results(tmp_path: Path) -> None:
    tasks = [
        EvalTask(task_id="t1", repo=".", base_commit=None, prompt="p"),
    ]

    results = run_suite(
        tasks,
        arm=ExperimentArm.SOGI,
        runner=MockRunner(),
        agent_label="mock",
        repeats=2,
        isolate=False,
    )

    assert len(results) == 2
    for result in results:
        assert result.arm == "sogi"
        assert result.success is True
        assert result.workspace_isolated is False
        assert result.verification_outcome is None
        # Mock reports no usage: fields must stay unreported, never estimated.
        assert result.input_tokens is None
        assert result.cost_usd is None


def test_arm_summary_and_comparison() -> None:
    baseline = [
        {"arm": "baseline", "success": True, "duration_seconds": 10.0},
        {"arm": "baseline", "success": False, "duration_seconds": 20.0},
    ]
    sogi = [
        {
            "arm": "sogi",
            "success": True,
            "duration_seconds": 12.0,
            "input_tokens": 1000,
            "output_tokens": 500,
            "verification_outcome": "PASS",
        }
    ]
    baseline_path = Path("/tmp/unused-b.jsonl")
    sogi_path = Path("/tmp/unused-s.jsonl")
    report = {
        "baseline": summarize("baseline", baseline).to_dict(),
        "sogi": summarize("sogi", sogi).to_dict(),
        "output_token_delta_pct": None,
        "note": "n",
    }
    assert report["baseline"]["trials"] == 2
    assert report["baseline"]["success_rate"] == 0.5
    assert report["sogi"]["total_input_tokens"] == 1000
    assert report["sogi"]["verification_outcomes"] == {"PASS": 1}
    assert report["sogi"]["verified_success_rate"] == 1.0
    assert report["sogi"]["verified_success_ci95"] is not None
    text = render(report)
    assert "token delta: unavailable" in text

    # JSONL round trip through compare()
    baseline_file = baseline_path.with_name("b.jsonl")
    sogi_file = sogi_path.with_name("s.jsonl")
    baseline_file.write_text("\n".join(json.dumps(item) for item in baseline) + "\n")
    sogi_file.write_text("\n".join(json.dumps(item) for item in sogi) + "\n")
    full = compare(baseline_file, sogi_file)
    assert full["baseline"]["trials"] == 2
    assert full["sogi"]["verification_outcomes"] == {"PASS": 1}


def test_cli_eval_run_and_compare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], suite: Path
) -> None:
    out = tmp_path / "results" / "out.jsonl"

    code = main(["eval", "run", str(suite), "--arm", "baseline", "--mock", "--out", str(out)])
    assert code == 0
    code = main(["eval", "run", str(suite), "--arm", "sogi", "--mock", "--out", str(out)])
    assert code == 0

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 4
    assert {json.loads(line)["arm"] for line in lines} == {"baseline", "sogi"}

    code = main(["eval", "compare", str(out), str(out)])
    assert code == 0
    assert "EXPERIMENT COMPARISON" in capsys.readouterr().out


def test_controlled_trials_are_revision_pinned_and_isolated(
    tmp_path: Path, eval_git_repo: tuple[Path, str]
) -> None:
    repo, head = eval_git_repo
    task = EvalTask(
        task_id="isolated",
        repo=str(repo),
        base_commit=head,
        prompt="Change the value",
    )
    workspaces: list[str] = []
    inherited_changes: list[bool] = []

    def runner(trial: EvalTask, arm: ExperimentArm) -> RunnerOutcome:
        trial_root = Path(trial.repo)
        workspaces.append(trial.repo)
        inherited_changes.append((trial_root / "from-previous-trial.txt").exists())
        (trial_root / "app.py").write_text("VALUE = 2\n")
        (trial_root / "from-previous-trial.txt").write_text(arm.value)
        return RunnerOutcome(exit_code=0, output="agent completed")

    artifacts = tmp_path / "artifacts"
    results = run_suite(
        [task],
        arm=ExperimentArm.BASELINE,
        runner=runner,
        repeats=2,
        artifacts_dir=artifacts,
    )

    assert inherited_changes == [False, False]
    assert len(set(workspaces)) == 2
    assert all(result.base_commit == head for result in results)
    assert all(result.workspace_isolated for result in results)
    assert all(result.patch_fingerprint for result in results)
    assert all(result.verification_outcome == "PASS" for result in results)
    assert all(result.verified_success is True for result in results)
    assert all(Path(result.patch_artifact or "").is_file() for result in results)
    assert all(
        "from-previous-trial.txt" in Path(result.patch_artifact or "").read_text()
        for result in results
    )
    assert all(
        Path(result.output_artifact or "").read_text() == "agent completed" for result in results
    )


def test_sogi_supervision_and_grading_use_separate_runs(
    eval_git_repo: tuple[Path, str],
) -> None:
    repo, head = eval_git_repo
    task = EvalTask(task_id="sogi", repo=str(repo), base_commit=head, prompt="Inspect the app")

    results = run_suite(
        [task],
        arm=ExperimentArm.SOGI,
        runner=MockRunner(),
    )

    result = results[0]
    assert result.run_id is not None
    assert result.grader_run_id is not None
    assert result.run_id != result.grader_run_id
    assert result.verification_outcome == "PASS"
    assert result.verification_report is not None
    assert result.verification_report["checks"][0]["command"] == "make test"
    assert result.patch_assessment is not None
    assert result.verified_success is True


def test_controlled_trial_requires_base_commit(eval_git_repo: tuple[Path, str]) -> None:
    repo, _ = eval_git_repo
    task = EvalTask(task_id="missing-base", repo=str(repo), base_commit=None, prompt="p")

    with pytest.raises(EvalWorkspaceError, match="base_commit"):
        run_suite([task], arm=ExperimentArm.BASELINE, runner=MockRunner())


def test_cli_controlled_trial_writes_auditable_result(
    tmp_path: Path,
    eval_git_repo: tuple[Path, str],
) -> None:
    repo, head = eval_git_repo
    suite_path = tmp_path / "controlled.json"
    suite_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "cli-controlled",
                        "repo": str(repo),
                        "base_commit": head,
                        "prompt": "Inspect the app",
                    }
                ]
            }
        )
    )
    output = tmp_path / "results.jsonl"

    code = main(
        [
            "eval",
            "run",
            str(suite_path),
            "--arm",
            "baseline",
            "--runner",
            "true",
            "--out",
            str(output),
        ]
    )

    assert code == 0
    result = json.loads(output.read_text())
    assert result["workspace_isolated"] is True
    assert result["base_commit"] == head
    assert result["verification_outcome"] == "PASS"
    assert result["verification_report"]["checks"][0]["command"] == "make test"
