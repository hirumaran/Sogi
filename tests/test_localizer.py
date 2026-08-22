"""Tests for hierarchical localization (file → symbol → region, tiered)."""

from pathlib import Path

from sogi.context.localizer import (
    HIGH,
    MEDIUM,
    RISK_DEPENDENCY,
    Localization,
    Localizer,
)
from sogi.core.task_spec import TaskSpec
from sogi.repository.provider import RepositoryProvider, RepositorySnapshot, Symbol


class RichFakeProvider(RepositoryProvider):
    """Deterministic provider with callers, tests, and varied relevance."""

    def prepare(self) -> dict:
        return {"total_symbols": 5}

    def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
        return RepositorySnapshot(
            task=task,
            symbols=(
                Symbol(
                    name="refresh_token",
                    file="src/auth/refresh.py",
                    line=82,
                    end_line=118,
                    content="def refresh_token():\n    ...\n",
                    relevance=1.0,
                ),
                Symbol(
                    name="test_expired_refresh",
                    file="tests/auth/test_refresh.py",
                    line=41,
                    end_line=67,
                    content="def test_expired_refresh():\n    ...\n",
                    relevance=0.8,
                ),
                Symbol(
                    name="validate_token",
                    file="src/auth/token.py",
                    line=30,
                    end_line=71,
                    content="def validate_token():\n    ...\n",
                    relevance=0.5,
                ),
            ),
            related_files=(
                "src/auth/refresh.py",
                "src/auth/token.py",
                "tests/auth/test_refresh.py",
            ),
            related_tests=("tests/auth/test_refresh.py",),
        )

    def search_symbols(self, query: str, *, limit: int = 20) -> tuple[Symbol, ...]:
        return ()

    def get_symbol(self, symbol: str, *, file: str | None = None) -> Symbol | None:
        return None

    def callers(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        if symbol == "refresh_token":
            return (
                Symbol(
                    name="OAuthHandler.callback",
                    file="src/auth/oauth.py",
                    line=151,
                    end_line=201,
                    kind="method",
                ),
            )
        return ()

    def callees(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        return ()

    def dependencies(self, file: str | None = None) -> dict:
        return {}

    def related_tests(self, files: tuple[str, ...]) -> tuple[str, ...]:
        return ("tests/auth/test_refresh.py",)


def make_task() -> TaskSpec:
    return TaskSpec.from_prompt("Fix expired refresh token handling")


def test_localization_tiers_symbols_and_callers() -> None:
    localization = Localizer(RichFakeProvider(Path("."))).localize(make_task(), prepare=False)

    tiers = [entry.tier for entry in localization.entries]
    assert tiers[0] == HIGH
    assert HIGH in tiers and MEDIUM in tiers and RISK_DEPENDENCY in tiers

    # The strongest matches lead; the protected caller comes after the core.
    names = [entry.symbol.name for entry in localization.entries]
    assert {"refresh_token", "test_expired_refresh"} <= set(names[:2])
    assert "OAuthHandler.callback" in names
    risk_entry = next(
        entry for entry in localization.entries if entry.tier == RISK_DEPENDENCY
    )
    assert "verify behavior stays intact" in risk_entry.reason

    # Exact regions are carried through.
    core = next(entry for entry in localization.entries if entry.symbol.name == "refresh_token")
    assert core.region == "lines 82-118"
    assert core.symbol.file == "src/auth/refresh.py"


def test_render_groups_entries_under_tier_headers() -> None:
    localization = Localizer(RichFakeProvider(Path("."))).localize(make_task(), prepare=False)

    rendered = localization.render()
    assert rendered.startswith("SOGI LOCALIZATION")
    for header in (HIGH, MEDIUM, RISK_DEPENDENCY):
        assert header in rendered
    assert "src/auth/oauth.py" in rendered


def test_empty_discover_yields_honest_localization() -> None:
    class EmptyProvider(RichFakeProvider):
        def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
            return RepositorySnapshot(task=task, symbols=())

    localization = Localizer(EmptyProvider(Path("."))).localize(make_task(), prepare=False)

    assert localization.entries == ()
    assert "No candidates" in localization.render()
    assert isinstance(localization, Localization)
