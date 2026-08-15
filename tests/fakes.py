"""Shared test doubles for Sogi tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sogi.repository.provider import RepositoryProvider, RepositorySnapshot, Symbol


class FakeProvider(RepositoryProvider):
    """Deterministic repository provider for tests that need no analyzer."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)

    def prepare(self) -> dict[str, Any]:
        return {"total_symbols": 3}

    def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
        return RepositorySnapshot(
            task=task,
            symbols=(
                Symbol(
                    name="refresh_token",
                    file="src/auth.py",
                    line=10,
                    end_line=12,
                    content="def refresh_token():\n    return validate_token()",
                    relevance=1.0,
                ),
                Symbol(
                    name="test_refresh_token",
                    file="tests/test_auth.py",
                    line=5,
                    content="def test_refresh_token():\n    assert True",
                    relevance=0.8,
                ),
            ),
            related_files=("src/auth.py", "tests/test_auth.py"),
            related_tests=("tests/test_auth.py",),
        )

    def search_symbols(self, query: str, *, limit: int = 20) -> tuple[Symbol, ...]:
        return ()

    def get_symbol(self, symbol: str, *, file: str | None = None) -> Symbol | None:
        return None

    def callers(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        return ()

    def callees(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        return ()

    def dependencies(self, file: str | None = None) -> dict[str, Any]:
        return {}

    def related_tests(self, files: tuple[str, ...]) -> tuple[str, ...]:
        return ("tests/test_auth.py",)
