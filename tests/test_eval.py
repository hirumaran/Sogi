"""Tests for the evaluation harness."""

import json
from pathlib import Path

import pytest

from sogi.cli import main
from sogi.eval import ExperimentArm, MockRunner, run_suite
from sogi.eval.compare import compare, render, summarize
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
    )

    assert len(results) == 2
    for result in results:
        assert result.arm == "sogi"
        assert result.success is True
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
