"""Tests for the content-sensitive worktree fingerprint.

The fingerprint must hash actual diff *contents*, not just the set of changed
filenames. Otherwise an agent can verify, modify an already-dirty file again,
and complete while the filename set (and thus the old fingerprint) is unchanged
— Sogi would accept stale evidence.
"""

import subprocess
from pathlib import Path

import pytest

from sogi.repository.worktree import capture_fingerprint


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def validate():\n    return True\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def test_non_git_dir_degrades_to_none(tmp_path: Path) -> None:
    repo = tmp_path / "notgit"
    repo.mkdir()
    fingerprint = capture_fingerprint(repo)
    assert fingerprint.git_head is None
    assert fingerprint.diff_hash is None


def test_clean_tree_fingerprint_is_stable(git_repo: Path) -> None:
    assert capture_fingerprint(git_repo) == capture_fingerprint(git_repo)


def test_reediting_dirty_file_changes_fingerprint(git_repo: Path) -> None:
    """The core fix: same filename, new bytes -> different fingerprint."""
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")
    after_first_edit = capture_fingerprint(git_repo)

    # The agent "verifies" here (snapshot pinned to after_first_edit), then
    # edits the SAME already-dirty file again with different content.
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return True  # patched\n")
    after_second_edit = capture_fingerprint(git_repo)

    assert after_first_edit != after_second_edit
    assert after_first_edit.git_head == after_second_edit.git_head


def test_identical_content_produces_identical_fingerprint(git_repo: Path) -> None:
    """Fingerprint is content-deterministic, not time- or order-dependent."""
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")
    first = capture_fingerprint(git_repo)
    # Rewrite with identical bytes (mtime changes, content does not).
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")
    second = capture_fingerprint(git_repo)
    assert first == second


def test_staged_and_unstaged_both_counted(git_repo: Path) -> None:
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")
    unstaged_only = capture_fingerprint(git_repo)

    git(git_repo, "add", "src/auth.py")
    staged = capture_fingerprint(git_repo)
    # Moving the same content from unstaged to staged changes the diff shape,
    # so the fingerprint must move with it.
    assert unstaged_only != staged


def test_untracked_file_content_is_folded_in(git_repo: Path) -> None:
    new_file = git_repo / "src" / "billing.py"
    new_file.write_text("price = 10\n")
    with_untracked = capture_fingerprint(git_repo)

    # Same path, different content -> different fingerprint.
    new_file.write_text("price = 99\n")
    with_changed_untracked = capture_fingerprint(git_repo)
    assert with_untracked != with_changed_untracked


def test_head_change_is_captured(git_repo: Path) -> None:
    """A new commit moves git_head even if the working tree is left clean."""
    before = capture_fingerprint(git_repo)
    (git_repo / "src" / "auth.py").write_text("def validate():\n    return False\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-qm", "second")
    after = capture_fingerprint(git_repo)
    assert before.git_head != after.git_head
