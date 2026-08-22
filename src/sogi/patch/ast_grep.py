"""AstGrepPatchProvider: AST-aware structural rewrites via the ast-grep CLI.

ast-grep searches and rewrites code by syntax-tree structure rather than text,
so a pattern like ``$A + $B`` cannot accidentally match inside strings or
comments. Sogi uses it as the low-level engine for pattern rewrites while
keeping every policy decision (scope, staleness, application) on the Sogi side.

CLI contract relied upon:

- ``sg run -p <pattern> -r <rewrite> [paths...]``  prints a preview diff and
  changes nothing (dry run).
- appending ``-U`` applies the rewrite to the matched files.

Only the documented CLI surface is used; the ast-grep source checkout under
``external/ast-grep`` is reference material, never imported.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .provider import AppliedPatch, PatchError, PatchProposal, PatchProvider, PatchToolUnavailable

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

_PREVIEW_TIMEOUT = 60
_APPLY_TIMEOUT = 120


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), capture_output=True, text=True, check=False, timeout=_APPLY_TIMEOUT
    )


def detect_ast_grep() -> str | None:
    return shutil.which("ast-grep") or shutil.which("sg")


class AstGrepPatchProvider(PatchProvider):
    """Pattern→rewrite operations delegated to the ast-grep binary."""

    def __init__(
        self,
        repo_root: Path,
        *,
        command: str | None = None,
        runner: CommandRunner = _default_runner,
    ) -> None:
        super().__init__(repo_root)
        self._command = command or detect_ast_grep()
        self._runner = runner

    @property
    def available(self) -> bool:
        return self._command is not None

    def locate_target(self, request: dict) -> None:
        """Pattern rewrites are location-free; staleness uses worktree hashes."""
        return None

    def dry_run(self, request: dict) -> PatchProposal:
        pattern = request.get("pattern")
        rewrite = request.get("rewrite")
        if not pattern:
            raise PatchError("rewrite requests need a 'pattern'")
        if rewrite is None:
            raise PatchError("rewrite requests need a 'rewrite'")
        if not self.available:
            raise PatchToolUnavailable(
                "ast-grep is not installed; pattern rewrites are unavailable "
                "(install with `brew install ast-grep` or `cargo install ast-grep-cli`)"
            )
        completed = self._run_preview(pattern, rewrite, request.get("paths"))
        if completed.returncode != 0:
            raise PatchError(f"ast-grep failed: {_detail(completed)}")
        diff = completed.stdout
        return PatchProposal(
            operation="rewrite",
            files=tuple(_files_from_sg_diff(diff)),
            diff=diff,
        )

    def apply(self, request: dict) -> AppliedPatch:
        # Recompute the proposal so apply is exactly what was previewed, then
        # let the caller-side RunService compare it against the stored diff.
        proposal = self.dry_run(request)
        assert self._command is not None
        paths = request.get("paths") or []
        command = [
            self._command,
            "run",
            "-p",
            request["pattern"],
            "-r",
            request["rewrite"],
            "-U",
            *paths,
        ]
        try:
            completed = self._runner(command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PatchToolUnavailable(f"Could not execute ast-grep: {exc}") from exc
        if completed.returncode != 0:
            raise PatchError(f"ast-grep apply failed: {_detail(completed)}")
        return AppliedPatch(operation=proposal.operation, files=proposal.files, diff=proposal.diff)

    def _run_preview(
        self, pattern: str, rewrite: str, paths: Sequence[str] | None
    ) -> subprocess.CompletedProcess[str]:
        assert self._command is not None
        command = [
            self._command,
            "run",
            "-p",
            pattern,
            "-r",
            rewrite,
            *(paths or []),
        ]
        try:
            return self._runner(command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PatchToolUnavailable(f"Could not execute ast-grep: {exc}") from exc


def _files_from_sg_diff(diff: str) -> list[str]:
    """Extract file names from ast-grep's proprietary diff preview.

    Preview blocks look like::

        ---
        src/auth.py
        ------- -------
        @@ ...
    """
    lines = diff.splitlines()
    files: list[str] = []
    for index, line in enumerate(lines):
        if not _is_sg_separator(line) or index + 1 >= len(lines):
            continue
        candidate = lines[index + 1].strip()
        # A second dash-run precedes each hunk; its next line is "@@", not a path.
        if not candidate or candidate.startswith("@") or candidate in files:
            continue
        files.append(candidate)
    return files


def _is_sg_separator(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 3 and set(stripped.replace(" ", "")) == {"-"}


def _detail(completed: subprocess.CompletedProcess[str]) -> str:
    output = completed.stderr.strip() or completed.stdout.strip()
    return output.splitlines()[0] if output else f"exit {completed.returncode}"
