from pathlib import Path
from typing import Any

from sogi.context.compiler import ContextCompiler
from sogi.core.task_spec import TaskSpec
from sogi.repository.provider import RepositoryProvider, RepositorySnapshot, Symbol


class FakeProvider(RepositoryProvider):
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


def test_compiler_ranks_and_respects_budget(tmp_path: Path) -> None:
    provider = FakeProvider(tmp_path)
    task = TaskSpec.from_prompt("Fix expired refresh token redirect")

    compiled = ContextCompiler(provider, token_budget=80).compile(task)

    assert compiled.selected
    assert compiled.selected_tokens <= compiled.token_budget
    assert compiled.selected[0].symbol.name == "test_refresh_token"
    assert compiled.related_tests == ("tests/test_auth.py",)
    assert "SOGI CONTEXT" in compiled.render()


def test_compiler_truncates_single_oversized_candidate(tmp_path: Path) -> None:
    provider = FakeProvider(tmp_path)
    original_discover = provider.discover

    def oversized(task: str, *, limit: int = 30) -> RepositorySnapshot:
        snapshot = original_discover(task, limit=limit)
        symbol = snapshot.symbols[0]
        return RepositorySnapshot(
            task=task,
            symbols=(
                Symbol(
                    **{
                        **symbol.__dict__,
                        "content": "x" * 2000,
                    }
                ),
            ),
        )

    provider.discover = oversized  # type: ignore[method-assign]
    compiled = ContextCompiler(provider, token_budget=64).compile(
        TaskSpec.from_prompt("Fix refresh token")
    )

    assert len(compiled.selected) == 1
    assert compiled.selected_tokens <= 64
    assert "truncated by Sogi" in (compiled.selected[0].symbol.content or "")
