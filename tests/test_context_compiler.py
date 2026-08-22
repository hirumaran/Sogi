from pathlib import Path
from typing import Any

from sogi.context.compiler import ContextCompiler
from sogi.context.ranking import rank_symbol
from sogi.core.phases import EngineeringPhase
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


def test_phase_weights_prioritize_tests_during_verification() -> None:
    source = Symbol(
        name="refresh_token",
        file="src/session.py",
        line=1,
        content="def refresh_token(): pass",
        relevance=0.9,
    )
    test = Symbol(
        name="test_refresh_token",
        file="tests/test_session.py",
        line=1,
        content="def test_refresh_token(): assert True",
        relevance=0.6,
    )

    implementation_source = rank_symbol(
        source, ("refresh", "token"), phase=EngineeringPhase.IMPLEMENT
    )
    implementation_test = rank_symbol(test, ("refresh", "token"), phase=EngineeringPhase.IMPLEMENT)
    verification_source = rank_symbol(source, ("refresh", "token"), phase=EngineeringPhase.VERIFY)
    verification_test = rank_symbol(test, ("refresh", "token"), phase=EngineeringPhase.VERIFY)

    implementation_test_advantage = implementation_test.score - implementation_source.score
    verification_test_advantage = verification_test.score - verification_source.score

    assert verification_test_advantage > implementation_test_advantage
    assert verification_test.score > verification_source.score


def test_compiler_diversifies_selected_files(tmp_path: Path) -> None:
    provider = FakeProvider(tmp_path)

    def redundant(task: str, *, limit: int = 30) -> RepositorySnapshot:
        del task, limit
        return RepositorySnapshot(
            task="Fix refresh token",
            symbols=(
                Symbol(
                    name="refresh_token",
                    file="src/auth.py",
                    line=1,
                    content="def refresh_token(): return validate()",
                    relevance=1.0,
                ),
                Symbol(
                    name="refresh_token_helper",
                    file="src/auth.py",
                    line=4,
                    content="def refresh_token_helper(): return validate()",
                    relevance=0.99,
                ),
                Symbol(
                    name="validate_session",
                    file="src/session.py",
                    line=1,
                    content="def validate_session(): return refresh_token()",
                    relevance=0.90,
                ),
            ),
        )

    provider.discover = redundant  # type: ignore[method-assign]
    task = TaskSpec.from_prompt("Fix refresh token")
    candidates = [
        rank_symbol(symbol, task.concepts) for symbol in provider.discover(task.objective).symbols
    ]
    two_item_budget = sum(sorted(item.token_cost for item in candidates)[:2])

    compiled = ContextCompiler(provider, token_budget=max(64, two_item_budget)).compile(task)

    selected_files = [item.symbol.file for item in compiled.selected[:2]]
    assert selected_files == ["src/auth.py", "src/session.py"]
    assert compiled.selection_strategy == "phase-aware-mmr"
    assert compiled.phase == EngineeringPhase.INVESTIGATE.value


def test_compiled_context_round_trip_preserves_phase(tmp_path: Path) -> None:
    compiled = ContextCompiler(FakeProvider(tmp_path), token_budget=80).compile(
        TaskSpec.from_prompt("Fix refresh token"),
        phase=EngineeringPhase.REVIEW,
    )

    restored = type(compiled).from_dict(compiled.to_dict())

    assert restored.phase == EngineeringPhase.REVIEW.value
    assert restored.selection_strategy == "phase-aware-mmr"
