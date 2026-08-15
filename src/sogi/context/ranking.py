from __future__ import annotations

from dataclasses import dataclass

from sogi.repository.provider import Symbol

from .budget import estimate_tokens

_RISK_TERMS = {
    "auth",
    "credential",
    "database",
    "oauth",
    "payment",
    "permission",
    "schema",
    "security",
    "session",
    "token",
}


@dataclass(frozen=True)
class RankedContext:
    symbol: Symbol
    semantic_relevance: float
    dependency_relevance: float
    test_relevance: float
    risk_relevance: float
    score: float
    token_cost: int


def rank_symbol(symbol: Symbol, concepts: tuple[str, ...]) -> RankedContext:
    haystack = f"{symbol.name} {symbol.file} {symbol.content or ''}".lower()
    matches = sum(1 for concept in concepts if concept.lower() in haystack)
    lexical = matches / max(1, len(concepts))
    semantic = min(1.0, max(symbol.relevance, lexical))
    is_test = _is_test_path(symbol.file)
    dependency = 0.5 if symbol.content else 0.2
    test = 1.0 if is_test else 0.0
    risk = 1.0 if any(term in haystack for term in _RISK_TERMS) else 0.0
    score = 0.40 * semantic + 0.25 * dependency + 0.20 * test + 0.15 * risk
    rendered = render_symbol(symbol)
    return RankedContext(
        symbol=symbol,
        semantic_relevance=semantic,
        dependency_relevance=dependency,
        test_relevance=test,
        risk_relevance=risk,
        score=score,
        token_cost=estimate_tokens(rendered),
    )


def render_symbol(symbol: Symbol) -> str:
    location = f"{symbol.file}:{symbol.line}"
    if symbol.end_line and symbol.end_line != symbol.line:
        location += f"-{symbol.end_line}"
    content = symbol.content or "(source body unavailable)"
    return f"## {symbol.name} — {location}\n{content.rstrip()}\n"


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in f"/{normalized}" or name.startswith("test_") or ".test." in name
