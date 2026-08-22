"""Tests for the independent verification engine."""

import json
from pathlib import Path

import pytest
from fakes import exit_command

from sogi.cli import main
from sogi.context.compiler import CompiledContext
from sogi.core.run_record import RunRecord
from sogi.core.task_spec import TaskSpec
from sogi.runs.service import RunService
from sogi.verification.discovery import DiscoveredCheck, discover_checks
from sogi.verification.evidence import criterion_terms, map_criteria
from sogi.verification.verifier import CheckResult, Verifier

# -- helpers ------------------------------------------------------------------


def make_record(
    objective: str = "Fix expired refresh token redirect",
    criteria: tuple[str, ...] = (),
    *,
    related_files: tuple[str, ...] = ("src/auth.py", "src/refresh.py"),
    related_tests: tuple[str, ...] = ("tests/test_refresh.py", "tests/test_session.py"),
) -> RunRecord:
    task = TaskSpec.from_prompt(objective, acceptance_criteria=criteria)
    from sogi.state.engineering_state import EngineeringState

    record = RunRecord(
        run_id="run1",
        task=task,
        state=EngineeringState(task_id="run1", objective=task.objective),
    )
    if related_files or related_tests:
        record.context = CompiledContext(
            task=task,
            selected=(),
            related_files=related_files,
            related_tests=related_tests,
            repository_estimated_tokens=100,
            candidate_tokens=50,
            selected_tokens=40,
            token_budget=4000,
            suggested_next_investigation="",
        )
    return record


def check(name: str, command: str, kind: str) -> DiscoveredCheck:
    return DiscoveredCheck(name=name, command=command, kind=kind)


def result(discovered: DiscoveredCheck, success: bool | None) -> CheckResult:
    return CheckResult(check=discovered, success=success, exit_code=0 if success else 1)


# -- discovery ----------------------------------------------------------------


def test_discovery_empty_repo(tmp_path: Path) -> None:
    assert discover_checks(tmp_path) == ()


def test_discovery_pyproject_pytest_and_ruff(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '-q'\n\n[tool.ruff]\nline-length = 100\n"
    )
    found = discover_checks(tmp_path)
    assert [(item.name, item.kind) for item in found] == [
        ("pytest", "test"),
        ("ruff", "lint"),
    ]


def test_discovery_mypy_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    found = discover_checks(tmp_path)
    assert [item.name for item in found] == ["mypy"]


def test_discovery_pytest_ini_without_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    found = discover_checks(tmp_path)
    assert [(item.name, item.kind) for item in found] == [("pytest", "test")]


def test_discovery_package_json_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest",
                    "build": "vite build",
                    "deploy": "vercel deploy",
                }
            }
        )
    )
    found = discover_checks(tmp_path)
    assert [(item.name, item.kind) for item in found] == [
        ("npm test", "test"),
        ("npm build", "build"),
    ]


def test_discovery_makefile_test_target(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\tpython -m pytest\n\n.PHONY: test\n")
    found = discover_checks(tmp_path)
    assert [item.command for item in found] == ["make test"]


def test_discovery_cargo(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    found = discover_checks(tmp_path)
    assert [(item.name, item.kind) for item in found] == [("cargo test", "test")]


# -- evidence mapping ---------------------------------------------------------


def test_criterion_terms_extracts_words() -> None:
    terms = criterion_terms("Expired refresh tokens redirect to /login")
    assert "expired" in terms
    assert "refresh" in terms
    assert "the" not in terms


def test_satisfied_with_matching_executed_passing_tests() -> None:
    record = make_record(criteria=("Expired refresh tokens redirect to /login",))
    results = (result(check("pytest", "pytest", "test"), True),)
    mapped = map_criteria(record, results)
    assert len(mapped) == 1
    assert mapped[0].status == "SATISFIED"
    assert mapped[0].evidence == ("tests/test_refresh.py",)


def test_violated_when_matching_tests_fail() -> None:
    record = make_record(criteria=("Expired refresh tokens redirect to /login",))
    results = (result(check("pytest", "pytest", "test"), False),)
    mapped = map_criteria(record, results)
    assert mapped[0].status == "VIOLATED"


def test_unverified_when_matching_test_not_executed() -> None:
    record = make_record(criteria=("Expired refresh tokens redirect to /login",))
    results = (result(check("ruff", "ruff check .", "lint"), True),)
    mapped = map_criteria(record, results)
    assert mapped[0].status == "UNVERIFIED"
    assert mapped[0].evidence == ("tests/test_refresh.py",)
    assert "not executed" in mapped[0].note


def test_unverified_without_matching_evidence() -> None:
    record = make_record(
        criteria=("Billing invoices round correctly",),
        related_tests=("tests/test_auth.py",),
    )
    results = (result(check("pytest", "pytest", "test"), True),)
    mapped = map_criteria(record, results)
    assert mapped[0].status == "UNVERIFIED"
    assert mapped[0].evidence == ()


def test_criteria_untouched_by_failing_lint() -> None:
    # A lint failure fails the report but does not by itself prove any
    # acceptance criterion violated.
    record = make_record(criteria=("Expired refresh tokens redirect to /login",))
    results = (
        result(check("pytest", "pytest", "test"), True),
        result(check("ruff", "ruff check .", "lint"), False),
    )
    mapped = map_criteria(record, results)
    assert all(item.status == "SATISFIED" for item in mapped)


# -- verifier execution -------------------------------------------------------


PASS_CHECK = check("pass", exit_command(0), "test")
FAIL_CHECK = check("fail", exit_command(3), "test")


def test_verifier_runs_checks_and_reports_pass(tmp_path: Path) -> None:
    record = make_record()
    report = Verifier(tmp_path).verify(record, checks=(PASS_CHECK,))
    assert report.outcome == "PASS"  # checks passed; no criteria left unproven
    assert report.checks[0].success is True
    assert report.passed is True


def test_verifier_fail_outcome_on_failed_check(tmp_path: Path) -> None:
    record = make_record()
    report = Verifier(tmp_path).verify(record, checks=(FAIL_CHECK,))
    assert report.outcome == "FAIL"
    assert report.checks[0].success is False
    assert report.checks[0].exit_code == 3


def test_verifier_inconclusive_without_checks(tmp_path: Path) -> None:
    report = Verifier(tmp_path).verify(make_record(), checks=())
    assert report.outcome == "INCONCLUSIVE"
    assert report.notes


def test_verifier_unknown_command_is_unavailable_not_failure(tmp_path: Path) -> None:
    # A missing executable is an environment fact (exit 127), not evidence
    # that the code fails its requirements — so it is reported as
    # unexecuted rather than failed, and cannot fabricate a FAIL.
    bogus = check("bogus", "sogi-definitely-not-a-command-xyz --version", "test")
    report = Verifier(tmp_path).verify(make_record(), checks=(bogus,))
    assert report.outcome == "INCONCLUSIVE"
    assert report.checks[0].success is None
    assert report.checks[0].execution_status == "blocked"
    assert "not allowed" in report.checks[0].output_tail


def test_verifier_render_surfaces_execution_policy_reason(tmp_path: Path) -> None:
    blocked = check("unsafe", "python -V && touch escaped", "test")

    report = Verifier(tmp_path).verify(make_record(), checks=(blocked,))

    rendered = report.render()
    assert "[blocked]" in rendered
    assert "shell operator" in rendered


def test_verifier_tool_missing_does_not_fail_criteria(tmp_path: Path) -> None:
    record = make_record(criteria=("Expired refresh tokens redirect to /login",))
    missing = check("pytest", "sogi-missing-tool-xyz", "test")
    report = Verifier(tmp_path).verify(record, checks=(missing,))
    assert report.outcome == "INCONCLUSIVE"
    # The criterion stays UNVERIFIED with its matching evidence listed.
    assert report.criteria[0].status == "UNVERIFIED"
    assert report.criteria[0].evidence == ("tests/test_refresh.py",)


def test_verifier_pass_with_unverified(tmp_path: Path) -> None:
    record = make_record(criteria=("Expired tokens redirect to /login",))
    report = Verifier(tmp_path).verify(record, checks=(PASS_CHECK,))
    assert report.outcome == "PASS_WITH_UNVERIFIED"


def test_verifier_render_shows_checks_and_criteria(tmp_path: Path) -> None:
    record = make_record(criteria=("Expired tokens redirect to /login",))
    report = Verifier(tmp_path).verify(record, checks=(PASS_CHECK,))
    text = report.render()
    assert f"[x] pass: {exit_command(0)}" in text
    assert "UNVERIFIED: Expired tokens redirect to /login" in text


# -- RunService integration ----------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture()
def service(repo: Path):
    from fakes import FakeProvider

    return RunService(repo, provider=FakeProvider(repo))


def test_service_verify_persists_evidence(service: RunService) -> None:
    run_id = service.start(
        "Fix auth",
        acceptance_criteria=("Auth behavior works",),
    ).run_id

    report = service.verify(run_id, checks=(check("t", exit_command(0), "test"),))

    assert report.outcome == "PASS"
    record = service.get(run_id)
    assert record.telemetry.commands[-1].success is True
    assert record.state.verification["Auth behavior works"] is True
    types = [event.type for event in service.events.for_run(run_id)]
    assert "verification_started" in types
    assert "verification_result" in types


def test_service_verify_failed_check_records_violated(service: RunService) -> None:
    run_id = service.start(
        "Fix auth",
        acceptance_criteria=("Auth behavior works",),
    ).run_id

    report = service.verify(run_id, checks=(check("t", exit_command(1), "test"),))

    record = service.get(run_id)
    assert report.outcome == "FAIL"
    assert record.state.verification["Auth behavior works"] is False


# -- CLI ------------------------------------------------------------------------


def test_cli_verify_json(capsys: pytest.CaptureFixture[str], repo: Path) -> None:
    with_run = RunService(repo)
    run_id = with_run.start("Fix auth", compile_context=False).run_id
    with_run.close()

    code = main(["verify", run_id, "--repo", str(repo), "--format", "json"])

    assert code == 1  # INCONCLUSIVE without discovered checks
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "INCONCLUSIVE"


def test_cli_verify_unknown_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["verify", "nope", "--repo", str(repo)])
    assert code == 1
    assert "nope" in capsys.readouterr().err
