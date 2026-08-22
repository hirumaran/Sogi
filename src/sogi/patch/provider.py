"""PatchProvider: Sogi's port for structural, governed code edits.

The separation that matters: a coding agent reasons about *what* semantic
change is required; Sogi owns *how* it is mechanically applied — target
resolution, stale-edit detection, dry-run diffs, scope checks, and atomic
writes. Providers never run unrestricted rewrites.

Two operations exist at v1:

- ``replace_symbol``  resolve a symbol through repository intelligence, hash
  its exact source region, and splice in a replacement body. The content hash
  gives real stale-edit protection against concurrent edits, stale context,
  or any other tool mutating the file between inspection and modification.
- ``rewrite``         AST pattern → rewrite via ast-grep
  (:class:`sogi.patch.ast_grep.AstGrepPatchProvider`).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from sogi.repository.provider import RepositoryProvider


class PatchError(RuntimeError):
    """Base class for patch-policy failures."""


class PatchToolUnavailable(PatchError):
    """The backing engine (e.g. ast-grep) is not installed."""


class StaleTargetError(PatchError):
    """Target changed since inspection; the agent must re-localize."""

    def __init__(self, detail: str, *, expected: str | None, observed: str | None) -> None:
        super().__init__(
            f"PATCH REJECTED: {detail} "
            f"(expected {expected or 'n/a'}, observed {observed or 'n/a'}). "
            "Re-localize the symbol before editing."
        )
        self.expected = expected
        self.observed = observed


def region_hash(content: str) -> str:
    """SHA-256 over the exact source region text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PatchTarget:
    """A resolved, hashed edit location (1-based inclusive line range)."""

    file: str
    start_line: int
    end_line: int
    content_hash: str
    symbol: str | None = None


@dataclass(frozen=True)
class PatchProposal:
    """A computed but not-yet-applied edit."""

    operation: str
    files: tuple[str, ...]
    diff: str
    target: PatchTarget | None = None


@dataclass(frozen=True)
class AppliedPatch:
    """Result of applying one proposal."""

    operation: str
    files: tuple[str, ...]
    diff: str


class PatchProvider(ABC):
    """Replaceable port for locating, previewing, and applying edits."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.expanduser().resolve()

    @abstractmethod
    def locate_target(self, request: dict) -> PatchTarget | None:
        """Resolve the edit location for a request, if it exists."""

    @abstractmethod
    def dry_run(self, request: dict) -> PatchProposal:
        """Compute the diff an application would produce, changing nothing."""

    @abstractmethod
    def apply(self, request: dict) -> AppliedPatch:
        """Apply the edit after re-validating staleness guards."""


class RegionPatchProvider(PatchProvider):
    """Symbol/region splicing with content-hash stale-edit protection."""

    def __init__(self, repo_root: Path, provider: RepositoryProvider) -> None:
        super().__init__(repo_root)
        self.repository = provider

    def locate_target(self, request: dict) -> PatchTarget | None:
        symbol_name = request.get("symbol")
        if not symbol_name:
            return None
        symbol = self.repository.get_symbol(str(symbol_name), file=request.get("file"))
        if symbol is None or not symbol.file:
            return None
        path = self._resolve(symbol.file)
        lines = _read_lines(path)
        start = max(1, symbol.line)
        end = min(len(lines), symbol.end_line or symbol.line)
        if start > end:
            raise PatchError(f"Symbol {symbol_name!r} has an invalid line range in {symbol.file}")
        region = "".join(lines[start - 1 : end])
        return PatchTarget(
            file=symbol.file,
            start_line=start,
            end_line=end,
            content_hash=region_hash(region),
            symbol=str(symbol_name),
        )

    def dry_run(self, request: dict) -> PatchProposal:
        target = self._validated_target(request)
        path = self._resolve(target.file)
        new_content = _spliced_text(
            path, target.start_line, target.end_line, request.get("replacement", "")
        )
        diff = _diff_for(target.file, path, new_content)
        return PatchProposal(
            operation="replace_symbol",
            files=(target.file,),
            diff=diff,
            target=target,
        )

    def apply(self, request: dict) -> AppliedPatch:
        proposal = self.dry_run(request)
        assert proposal.target is not None
        path = self._resolve(proposal.target.file)
        new_content = _spliced_text(
            path,
            proposal.target.start_line,
            proposal.target.end_line,
            request.get("replacement", ""),
        )
        path.write_text(new_content, encoding="utf-8")
        return AppliedPatch(operation=proposal.operation, files=proposal.files, diff=proposal.diff)

    def _validated_target(self, request: dict) -> PatchTarget:
        target = self.locate_target(request)
        if target is None:
            raise PatchError(f"Could not resolve target symbol: {request.get('symbol')!r}")
        expected = request.get("expected_hash")
        if expected and expected != target.content_hash:
            raise StaleTargetError(
                f"Target {target.symbol} changed since inspection.",
                expected=expected,
                observed=target.content_hash,
            )
        return target

    def _resolve(self, relative: str) -> Path:
        path = (self.repo_root / relative).resolve()
        if not path.is_relative_to(self.repo_root):
            raise PatchError(f"Target escapes the repository root: {relative}")
        return path


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise PatchError(f"Cannot read {path}: {exc}") from exc


def _spliced_text(path: Path, start_line: int, end_line: int, replacement: str) -> str:
    lines = _read_lines(path)
    replacement_lines = [line + "\n" for line in replacement.splitlines()]
    new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    return "".join(new_lines)


def _diff_for(relative: str, path: Path, new_content: str) -> str:
    old_lines = _read_lines(path)
    new_lines = new_content.splitlines(keepends=True)
    return "".join(
        unified_diff(old_lines, new_lines, fromfile=f"a/{relative}", tofile=f"b/{relative}")
    )
