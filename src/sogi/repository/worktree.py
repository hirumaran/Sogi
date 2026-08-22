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

#: Generated artifacts that appear in `git status` on repositories without a
#: .gitignore. They are tooling noise, not engineering changes, so they are
#: excluded from fingerprints and reconciliation alike.
TRANSIENT_PREFIXES = (
    ".sogi/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
)


def _is_transient(path: str) -> bool:
    return (
        path.startswith(TRANSIENT_PREFIXES)
        or "/__pycache__/" in path
        or path.endswith((".pyc", ".pyo"))
    )


def filter_transient(paths: list[str]) -> list[str]:
    return [path for path in paths if path and not _is_transient(path)]


@dataclass(frozen=True)
class WorktreeFingerprint:
    """A cheap deterministic view of repository mutation state."""

    git_head: str | None
    diff_hash: str | None

    def matches(self, other: WorktreeFingerprint | None) -> bool | None:
        """True/False when comparable; None when either side is unavailable."""
        if other is None or self.diff_hash is None or other.diff_hash is None:
            return None
        return self == other


def capture_fingerprint(repo_root: Path) -> WorktreeFingerprint:
    """Capture a content-sensitive fingerprint of the working tree.

    The hash covers the actual diff *contents* — not just the set of changed
    filenames — so re-editing an already-dirty file (same filenames, new bytes)
    produces a different fingerprint and the completion gate rejects the now-
    stale evidence. Streamed incrementally so large repositories stay cheap.

    Covers, in order: current HEAD, staged diff, unstaged diff, and each
    untracked file's path plus a streaming content hash. Non-Git repos degrade
    honestly to ``None`` (staleness then relies on the event-sequence watermark).
    """
    root = repo_root.expanduser().resolve()
    head = _git(root, "rev-parse", "HEAD")
    if not head:
        return WorktreeFingerprint(git_head=None, diff_hash=None)
    hasher = hashlib.sha256()
    _feed(hasher, b"head", head.encode("utf-8"))
    # Tracked changes: full unified-0 diff content for staged + unstaged.
    _feed(hasher, b"staged", (_git(root, "diff", "--cached", "--unified=0") or "").encode("utf-8"))
    _feed(hasher, b"unstaged", (_git(root, "diff", "--unified=0") or "").encode("utf-8"))
    # Untracked files: path plus a streaming per-file content hash (git diff
    # never includes untracked files, so they must be folded in explicitly).
    # Transient tooling artifacts are excluded so running the checks
    # themselves cannot invalidate their own verification.
    untracked = _git(root, "ls-files", "--others", "--exclude-standard") or ""
    for raw in filter_transient(untracked.splitlines()):
        path = raw.strip()
        if not path:
            continue
        _feed(hasher, b"untracked", path.encode("utf-8"))
        _fold_file(hasher, root / path)
    return WorktreeFingerprint(git_head=head, diff_hash=hasher.hexdigest()[:16])


def _feed(hasher: hashlib._Hash, tag: bytes, value: bytes) -> None:
    """Length-prefixed feed so adjacent chunks cannot collide (``ab|cd`` vs ``a|bcd``)."""
    hasher.update(tag)
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _fold_file(hasher: hashlib._Hash, path: Path) -> None:
    """Fold one file's content into the fingerprint via a streaming sub-hash."""
    sub = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                sub.update(chunk)
    except (OSError, ValueError):
        # Binary/unreadable/missing: the path is already folded above, so the
        # fingerprint still moves with this entry even if content is unavailable.
        hasher.update(b"<unreadable>")
        return
    hasher.update(sub.digest())


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
