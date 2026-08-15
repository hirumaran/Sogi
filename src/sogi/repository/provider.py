from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Symbol:
    name: str
    file: str
    line: int
    end_line: int | None = None
    kind: str = "symbol"
    language: str | None = None
    content: str | None = None
    relevance: float = 0.0


@dataclass(frozen=True)
class RepositorySnapshot:
    task: str
    symbols: tuple[Symbol, ...]
    related_files: tuple[str, ...] = ()
    related_tests: tuple[str, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)


class RepositoryProvider(ABC):
    """Replaceable port for repository intelligence."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.expanduser().resolve()

    @abstractmethod
    def prepare(self) -> dict[str, Any]:
        """Prepare or incrementally refresh repository intelligence."""

    @abstractmethod
    def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
        """Return the smallest useful first-pass context for a task."""

    @abstractmethod
    def search_symbols(self, query: str, *, limit: int = 20) -> tuple[Symbol, ...]:
        pass

    @abstractmethod
    def get_symbol(self, symbol: str, *, file: str | None = None) -> Symbol | None:
        pass

    @abstractmethod
    def callers(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        pass

    @abstractmethod
    def callees(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        pass

    @abstractmethod
    def dependencies(self, file: str | None = None) -> dict[str, Any]:
        pass

    @abstractmethod
    def related_tests(self, files: tuple[str, ...]) -> tuple[str, ...]:
        pass
