from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .provider import RepositoryProvider, RepositorySnapshot, Symbol

CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


class AnalyzerCommandError(RuntimeError):
    pass


def _default_runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _detect_command() -> tuple[str, ...]:
    configured = os.environ.get("SOGI_TSA_COMMAND")
    if configured:
        return tuple(shlex.split(configured))
    installed = shutil.which("tree-sitter-analyzer")
    if installed:
        return (installed,)
    workspace = Path(__file__).resolve().parents[3]
    local = workspace / "tree-sitter-analyzer" / ".venv" / "bin" / "tree-sitter-analyzer"
    if local.exists():
        return (str(local),)
    return ("uvx", "--from", "tree-sitter-analyzer", "tree-sitter-analyzer")


class TreeSitterProvider(RepositoryProvider):
    """Tree-sitter Analyzer adapter that depends only on its public CLI JSON."""

    def __init__(
        self,
        project_root: Path,
        *,
        command: Sequence[str] | None = None,
        runner: CommandRunner = _default_runner,
    ) -> None:
        super().__init__(project_root)
        if not self.project_root.is_dir():
            raise ValueError(f"Repository does not exist: {self.project_root}")
        self.command = tuple(command or _detect_command())
        self._runner = runner

    def _run(self, *arguments: str) -> dict[str, Any]:
        command = [
            *self.command,
            "--project-root",
            str(self.project_root),
            *arguments,
            "--format",
            "json",
        ]
        try:
            completed = self._runner(command, self.project_root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AnalyzerCommandError(f"Could not run Tree-sitter Analyzer: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise AnalyzerCommandError(f"Tree-sitter Analyzer failed: {detail}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AnalyzerCommandError("Tree-sitter Analyzer returned invalid JSON") from exc

    def prepare(self) -> dict[str, Any]:
        return self._run("--full-index", "--full-index-mode", "incremental")

    def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
        payload = self._run(
            "--codegraph-context",
            task,
            "--codegraph-context-max-nodes",
            str(limit),
            "--codegraph-context-max-code-blocks",
            str(min(limit, 12)),
        )
        entry_names = {item.get("name") for item in payload.get("entry_points", [])}
        blocks = payload.get("code_blocks", [])
        symbols = tuple(
            Symbol(
                name=str(item.get("name", "unknown")),
                file=str(item.get("file", "")),
                line=int(item.get("start_line", 1)),
                end_line=_optional_int(item.get("end_line")),
                content=item.get("content"),
                relevance=1.0 if item.get("name") in entry_names else max(0.2, 0.8 - index * 0.05),
            )
            for index, item in enumerate(blocks)
            if item.get("file")
        )
        related_files = tuple(str(path) for path in payload.get("related_files", []))
        tests = tuple(path for path in related_files if _is_test_path(path))
        return RepositorySnapshot(
            task=task,
            symbols=symbols,
            related_files=related_files,
            related_tests=tests,
            stats=dict(payload.get("stats", {})),
        )

    def search_symbols(self, query: str, *, limit: int = 20) -> tuple[Symbol, ...]:
        payload = self._run("--symbol-search", query, "--symbol-search-limit", str(limit))
        return tuple(_symbol_from_item(item) for item in payload.get("results", []))

    def get_symbol(self, symbol: str, *, file: str | None = None) -> Symbol | None:
        arguments = ["--codegraph-navigate", symbol, "--codegraph-navigate-mode", "definition"]
        if file:
            arguments.extend(("--codegraph-navigate-file", file))
        payload = self._run(*arguments)
        definitions = payload.get("definition", {}).get("definitions", [])
        return _symbol_from_item(definitions[0]) if definitions else None

    def callers(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        arguments = ["--callers", symbol]
        if file:
            arguments.extend(("--callers-file", file))
        payload = self._run(*arguments)
        return tuple(_symbol_from_item(item) for item in payload.get("callers", []))

    def callees(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        arguments = ["--callees", symbol]
        if file:
            arguments.extend(("--callees-file", file))
        payload = self._run(*arguments)
        return tuple(_symbol_from_item(item) for item in payload.get("callees", []))

    def dependencies(self, file: str | None = None) -> dict[str, Any]:
        if file:
            return self._run(file, "--dependencies", "file_deps")
        return self._run("--dependencies", "summary")

    def related_tests(self, files: tuple[str, ...]) -> tuple[str, ...]:
        if not files:
            return ()
        payload = self._run("--affected", *files)
        return tuple(str(path) for path in payload.get("test_files", []))


def _symbol_from_item(item: dict[str, Any]) -> Symbol:
    body = item.get("body") or {}
    return Symbol(
        name=str(item.get("name", body.get("name", "unknown"))),
        file=str(item.get("file", body.get("file", ""))),
        line=int(item.get("line", body.get("start_line", 1))),
        end_line=_optional_int(item.get("end_line", body.get("end_line"))),
        kind=str(item.get("kind", "symbol")),
        language=item.get("language"),
        content=body.get("content") or item.get("content") or item.get("code"),
        relevance=float(item.get("relevance_score", 0.0)),
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in f"/{normalized}" or name.startswith("test_") or ".test." in name
