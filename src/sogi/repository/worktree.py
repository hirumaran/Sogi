"""Worktree fingerprinting for verification staleness detection.

Independent evidence is only valid while the repository is unchanged. This
module captures a cheap, deterministic fingerprint of repository state so the
completion gate can reject ``verify → edit → complete`` sequences.

Git repositories use status/diff output; non-Git repositories degrade
honestly to ``None`` (staleness then relies on the event-sequence watermark).
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeFingerprint:
    """A cheap deterministic view of repository mutation state."""

    git_head: str | None
    diff_hash: str | None

    def matches(self, other: "WorktreeFingerprint | None") -> bool | None:
        """True/False when comparable; None when either side is unavailable."""
        if other is None or self.diff_hash is None or other.diff_hash is None:
            return None
        return self == other


def capture_fingerprint(repo_root: Path) -> WorktreeFingerprint:
    root = repo_root.expanduser().resolve()
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    changed = "\n".join(
        filter(
            None,
            [
                status,
                _git(root, "diff", "--name-only"),
                _git(root, "diff", "--cached", "--name-only"),
            ],
        )
    )
    diff_hash = (
        hashlib.sha256(changed.encode("utf-8")).hexdigest()[:16] if head else None
    )
    return WorktreeFingerprint(git_head=head, diff_hash=diff_hash)


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
