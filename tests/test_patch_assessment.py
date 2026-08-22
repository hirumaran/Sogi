"""Tests for deterministic patch assessment."""

import subprocess
from pathlib import Path

import pytest

from sogi.patch import analyze_patch


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "auth.py").write_text("def validate():\n    return True\n")
    (repo / "tests" / "test_auth.py").write_text(
        "from src.auth import validate\n\n\ndef test_validate():\n    assert validate()\n"
    )
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def test_clean_tree_is_low_risk(git_repo: Path) -> None:
    assessment = analyze_patch(git_repo)
    assert assessment.changed_files == []
    assert assessment.risk == "LOW"


def test_in_scope_change_is_expected_and_low_risk(git_repo: Path) -> None:
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")

    assessment = analyze_patch(git_repo, expected_files=("src/auth.py",))

    assert assessment.expected_files == ["src/auth.py"]
    assert assessment.unexpected_files == []
    assert assessment.risk == "LOW"


def test_unexpected_file_is_medium_risk(git_repo: Path) -> None:
    (git_repo / "src" / "billing.py").write_text("price = 10\n")

    assessment = analyze_patch(git_repo, expected_files=("src/auth.py",))

    assert assessment.unexpected_files == ["src/billing.py"]
    assert assessment.risk == "MEDIUM"


def test_deleted_test_detected_as_tampering_risk(git_repo: Path) -> None:
    (git_repo / "tests" / "test_auth.py").unlink()

    assessment = analyze_patch(git_repo)

    assert assessment.tests_deleted == ["tests/test_auth.py"]
    assert assessment.risk == "HIGH"


def test_weakened_test_detected(git_repo: Path) -> None:
    test_path = git_repo / "tests" / "test_auth.py"
    test_path.write_text(
        "from src.auth import validate\n\n\ndef test_validate():\n    pytest.skip('later')\n"
    )

    assessment = analyze_patch(git_repo)

    assert assessment.tests_weakened == ["tests/test_auth.py"]
    assert assessment.risk == "HIGH"


def test_assertion_removal_detected_as_weakening(git_repo: Path) -> None:
    test_path = git_repo / "tests" / "test_auth.py"
    test_path.write_text(
        "from src.auth import validate\n\n\ndef test_validate():\n    validate()\n"
    )

    assessment = analyze_patch(git_repo)

    assert "tests/test_auth.py" in assessment.tests_weakened


def test_dependency_manifest_flagged(git_repo: Path) -> None:
    manifest = git_repo / "pyproject.toml"
    original = manifest.read_text()
    manifest.write_text(original + '\ndependencies = ["requests>=2.0"]\n')

    assessment = analyze_patch(git_repo)

    assert "pyproject.toml" in assessment.dependency_changes
    assert assessment.risk in {"MEDIUM", "HIGH"}


def test_security_sensitive_path_is_high_risk(git_repo: Path) -> None:
    secret_path = git_repo / "src" / "secrets.py"
    secret_path.write_text("KEY = 'x'\n")

    assessment = analyze_patch(git_repo, expected_files=("src/auth.py",))

    assert any(path.startswith("src/secrets.py") for path, _ in assessment.risky_path_details)
    assert assessment.risk == "HIGH"
