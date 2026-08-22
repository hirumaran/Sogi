"""End-to-end tests for structured (node-level) verification evidence."""

import subprocess
import sys
from pathlib import Path

from test_verification import make_record

from sogi.verification.discovery import DiscoveredCheck
from sogi.verification.evidence_providers import (
    parse_junit_xml,
    pytest_command_wants_report,
)
from sogi.verification.verifier import Verifier


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "auth.py").write_text(
        'def refresh(token):\n    if not token:\n        return "/login"\n    return token\n'
    )
    (repo / "tests" / "test_refresh.py").write_text(
        "from src.auth import refresh\n\n"
        "def test_expired_token_redirects_to_login():\n"
        '    assert refresh("") == "/login"\n\n'
        "def test_other_behavior():\n"
        '    assert refresh("abc") == "abc"\n'
    )
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


PYTEST_CMD = f'"{sys.executable}" -m pytest -q'


def test_pytest_report_flag_detection() -> None:
    assert pytest_command_wants_report("python -m pytest -q")
    assert not pytest_command_wants_report("pytest --junitxml=out.xml")
    assert not pytest_command_wants_report("ruff check .")


def test_junit_parsing_extracts_node_ids(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={report}"],
        cwd=repo,
        capture_output=True,
    )
    tests = parse_junit_xml(report)
    node_ids = {item.nodeid for item in tests}
    assert any("test_expired_token_redirects_to_login" in node for node in node_ids)
    assert all(item.outcome == "passed" for item in tests)


def test_criterion_satisfied_via_executed_node_evidence(tmp_path: Path) -> None:
    """The criterion is proven by an executed passing test, not a filename."""
    repo = _init_repo(tmp_path)
    record = make_record(
        objective="Fix expired refresh token redirect",
        criteria=("Expired token redirects to /login",),
    )

    verifier = Verifier(repo)
    check = DiscoveredCheck(name="pytest", command=PYTEST_CMD, kind="test")
    report = verifier.verify(record, checks=(check,))

    assert report.checks[0].executed_tests, "structured evidence must be captured"
    criterion = report.criteria[0]
    assert criterion.status == "SATISFIED"
    assert any("test_expired_token_redirects_to_login" in item for item in criterion.evidence), (
        f"evidence must be node ids, got: {criterion.evidence}"
    )


def test_failing_matching_test_violates_criterion_at_node_level(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Break the exact behavior the criterion describes.
    path = repo / "src" / "auth.py"
    path.write_text(
        'def refresh(token):\n    if not token:\n        return "/oops"\n    return token\n'
    )
    record = make_record(
        objective="Fix expired refresh token redirect",
        criteria=("Expired token redirects to /login",),
    )

    verifier = Verifier(repo)
    check = DiscoveredCheck(name="pytest", command=PYTEST_CMD, kind="test")
    report = verifier.verify(record, checks=(check,))

    criterion = report.criteria[0]
    assert criterion.status == "VIOLATED"
    assert any("expired_token" in item for item in criterion.evidence)


def test_skipped_matching_test_stays_unverified(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "tests" / "test_refresh.py").write_text(
        "import pytest\nfrom src.auth import refresh\n\n"
        "@pytest.mark.skip(reason='not implemented')\n"
        "def test_expired_token_redirects_to_login():\n"
        '    assert refresh("") == "/login"\n'
    )
    record = make_record(
        objective="Fix expired refresh token redirect",
        criteria=("Expired token redirects to /login",),
    )

    verifier = Verifier(repo)
    check = DiscoveredCheck(name="pytest", command=PYTEST_CMD, kind="test")
    report = verifier.verify(record, checks=(check,))

    # A skipped relevant test cannot satisfy its criterion even though the
    # suite exits green.
    criterion = report.criteria[0]
    assert criterion.status == "UNVERIFIED"
    assert "skipped" in criterion.note.lower()
