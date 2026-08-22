"""Integration tests for automatic patch assessment wired into verify().

An agent cannot skip tampering/scope/dependency scrutiny by simply never
calling ``sogi patch``: verify() assesses the working tree itself, raises
HIGH/CRITICAL findings, and completion blocks until they are acknowledged.
This is Sogi's "unsupported done claims are rejected" loop, end to end.
"""

import subprocess
from pathlib import Path

import pytest

from sogi.runs.service import CompletionGateError, RunService


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "auth.py").write_text("def validate():\n    return True\n")
    (repo / "tests" / "test_auth.py").write_text(
        "from src.auth import validate\n\n\ndef test_validate():\n    assert validate()\n"
    )
    # RunService writes .sogi/ inside the repo; gitignore it so analyze_patch
    # does not flag Sogi's own bookkeeping as an out-of-scope change.
    (repo / ".gitignore").write_text(".sogi/\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def _passing() -> tuple:
    from sogi.verification.discovery import DiscoveredCheck

    return (DiscoveredCheck(name="t", command="exit 0", kind="test"),)


def test_verify_on_clean_tree_raises_no_findings(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id

    service.verify(run_id, checks=_passing())

    record = service.get(run_id)
    assert record.telemetry.patch_assessment is not None
    assert record.telemetry.patch_assessment["risk"] == "LOW"
    assert record.telemetry.patch_assessment["changed_files"] == []
    assert not record.telemetry.warnings


def test_verify_auto_flags_weakened_test_and_blocks_completion(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id

    # Agent weakens a test (tampering) without ever calling `sogi patch`.
    (git_repo / "tests" / "test_auth.py").write_text(
        "from src.auth import validate\n\n\ndef test_validate():\n    pytest.skip('later')\n"
    )

    service.verify(run_id, checks=_passing())

    record = service.get(run_id)
    assert record.telemetry.patch_assessment is not None
    assert record.telemetry.patch_assessment["risk"] == "HIGH"
    tampering = [
        w
        for w in record.telemetry.warnings
        if w.kind == "test_tampering" and w.subject == "tests/test_auth.py"
    ]
    assert len(tampering) == 1
    assert tampering[0].severity == "CRITICAL"

    # Passing verification alone is not enough: the unacknowledged CRITICAL
    # finding blocks completion.
    with pytest.raises(CompletionGateError) as excinfo:
        service.complete(run_id, allow_unverified=True)
    assert "Unresolved high-severity finding" in str(excinfo.value)


def test_verify_auto_flags_deleted_test(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id
    (git_repo / "tests" / "test_auth.py").unlink()

    service.verify(run_id, checks=_passing())

    record = service.get(run_id)
    assert any(
        w.kind == "test_tampering" and w.subject == "tests/test_auth.py"
        for w in record.telemetry.warnings
    )


def test_acknowledge_unblocks_completion_after_auto_assessment(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id
    (git_repo / "tests" / "test_auth.py").unlink()

    service.verify(run_id, checks=_passing())
    service.acknowledge(run_id, "test_tampering", "tests/test_auth.py")

    record = service.complete(run_id, allow_unverified=True)
    assert record.telemetry.outcome == "completed"
    assert record.state.phase.value == "done"
    ack_events = [
        e.payload
        for e in service.events.for_run(run_id)
        if e.type == "decision_recorded" and e.payload.get("kind") == "acknowledge"
    ]
    assert any(
        p.get("warning_kind") == "test_tampering" and p.get("subject") == "tests/test_auth.py"
        for p in ack_events
    )


def test_re_verify_does_not_duplicate_auto_findings(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id
    (git_repo / "tests" / "test_auth.py").unlink()

    service.verify(run_id, checks=_passing())
    service.verify(run_id, checks=_passing())  # re-verify

    tampering = [
        w
        for w in service.get(run_id).telemetry.warnings
        if w.kind == "test_tampering" and w.subject == "tests/test_auth.py"
    ]
    assert len(tampering) == 1  # deduped, not duplicated


def test_verify_auto_flags_dependency_change(git_repo: Path) -> None:
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id
    (git_repo / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')

    service.verify(run_id, checks=_passing())

    record = service.get(run_id)
    assert any(
        w.kind == "dependency_change" and w.subject == "pyproject.toml"
        for w in record.telemetry.warnings
    )
