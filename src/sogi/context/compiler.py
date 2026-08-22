from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sogi.core.phases import EngineeringPhase
from sogi.core.task_spec import TaskSpec
from sogi.repository.provider import RepositoryProvider, Symbol

from .budget import estimate_tokens, truncate_to_tokens
from .ranking import RankedContext, rank_symbol, render_symbol

_CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
}
_IGNORED_PARTS = {".git", ".venv", "build", "dist", "node_modules", "vendor"}


@dataclass(frozen=True)
class CompiledContext:
    task: TaskSpec
    selected: tuple[RankedContext, ...]
    related_files: tuple[str, ...]
    related_tests: tuple[str, ...]
    repository_estimated_tokens: int
    candidate_tokens: int
    selected_tokens: int
    token_budget: int
    suggested_next_investigation: str
    phase: str = EngineeringPhase.INVESTIGATE.value
    selection_strategy: str = "phase-aware-mmr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "context": [
                {
                    **asdict(item),
                    "symbol": asdict(item.symbol),
                }
                for item in self.selected
            ],
            "related_files": list(self.related_files),
            "related_tests": list(self.related_tests),
            "phase": self.phase,
            "selection_strategy": self.selection_strategy,
            "metrics": {
                "repository_estimated_tokens": self.repository_estimated_tokens,
                "candidate_tokens": self.candidate_tokens,
                "selected_tokens": self.selected_tokens,
                "token_budget": self.token_budget,
            },
            "suggested_next_investigation": self.suggested_next_investigation,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CompiledContext:
        task = TaskSpec.from_dict(payload["task"])
        selected = tuple(
            RankedContext(
                symbol=Symbol(**item["symbol"]),
                semantic_relevance=float(item["semantic_relevance"]),
                dependency_relevance=float(item["dependency_relevance"]),
                test_relevance=float(item["test_relevance"]),
                risk_relevance=float(item["risk_relevance"]),
                score=float(item["score"]),
                token_cost=int(item["token_cost"]),
            )
            for item in payload.get("context", [])
        )
        metrics = payload.get("metrics", {})
        return cls(
            task=task,
            selected=selected,
            related_files=tuple(str(path) for path in payload.get("related_files", ())),
            related_tests=tuple(str(path) for path in payload.get("related_tests", ())),
            repository_estimated_tokens=int(metrics.get("repository_estimated_tokens", 0)),
            candidate_tokens=int(metrics.get("candidate_tokens", 0)),
            selected_tokens=int(metrics.get("selected_tokens", 0)),
            token_budget=int(metrics.get("token_budget", 0)),
            suggested_next_investigation=str(payload.get("suggested_next_investigation", "")),
            phase=str(payload.get("phase", EngineeringPhase.INVESTIGATE.value)),
            selection_strategy=str(payload.get("selection_strategy", "legacy-score")),
        )

    def render(self) -> str:
        criteria = _render_list(self.task.acceptance_criteria, "Not explicitly provided")
        constraints = _render_list(self.task.constraints, "None explicitly provided")
        files = _render_list(self.related_files, "No related files found")
        tests = _render_list(self.related_tests, "No related tests found")
        context = "\n".join(render_symbol(item.symbol) for item in self.selected).rstrip()
        if not context:
            context = "No context fit the configured budget."
        risks = [item.symbol.file for item in self.selected if item.risk_relevance > 0]
        risk_text = _render_list(tuple(dict.fromkeys(risks)), "No deterministic risk signal found")
        return (
            "SOGI CONTEXT\n"
            "============\n\n"
            f"PHASE\n{self.phase.upper()}\n\n"
            f"OBJECTIVE\n{self.task.objective}\n\n"
            f"ACCEPTANCE CRITERIA\n{criteria}\n\n"
            f"CONSTRAINTS\n{constraints}\n\n"
            f"RELEVANT FILES\n{files}\n\n"
            f"RELATED TESTS\n{tests}\n\n"
            f"RISK-SENSITIVE FILES\n{risk_text}\n\n"
            f"SELECTED CONTEXT\n{context}\n\n"
            f"NEXT INVESTIGATION\n{self.suggested_next_investigation}\n\n"
            "TOKEN METRICS\n"
            f"Repository estimate: {self.repository_estimated_tokens}\n"
            f"Candidate context: {self.candidate_tokens}\n"
            f"Selected context: {self.selected_tokens}\n"
            f"Budget: {self.token_budget}"
        )


class ContextCompiler:
    def __init__(self, provider: RepositoryProvider, *, token_budget: int = 4000) -> None:
        if token_budget < 64:
            raise ValueError("Token budget must be at least 64")
        self.provider = provider
        self.token_budget = token_budget

    def compile(
        self,
        task: TaskSpec,
        *,
        prepare: bool = True,
        phase: EngineeringPhase | str = EngineeringPhase.INVESTIGATE,
    ) -> CompiledContext:
        """Compile bounded, phase-appropriate, non-redundant context."""
        resolved_phase = EngineeringPhase(phase)
        index_stats = self.provider.prepare() if prepare else {}
        snapshot = self.provider.discover(task.objective)
        ranked = sorted(
            (
                rank_symbol(symbol, task.concepts, phase=resolved_phase)
                for symbol in snapshot.symbols
            ),
            key=lambda item: (-item.score, item.token_cost, item.symbol.file, item.symbol.line),
        )
        selected = _select_under_budget(ranked, self.token_budget)
        tests = snapshot.related_tests
        source_files = tuple(path for path in snapshot.related_files if path not in tests)
        if source_files:
            discovered_tests = self.provider.related_tests(source_files)
            tests = tuple(dict.fromkeys((*tests, *discovered_tests)))
        repository_tokens = estimate_repository_tokens(self.provider.project_root)
        if not repository_tokens:
            repository_tokens = int(index_stats.get("total_symbols", 0)) * 24
        next_step = (
            f"Inspect {selected[0].symbol.name} at "
            f"{selected[0].symbol.file}:{selected[0].symbol.line}."
            if selected
            else "Increase the budget or refine the task concepts."
        )
        return CompiledContext(
            task=task,
            selected=selected,
            related_files=snapshot.related_files,
            related_tests=tests,
            repository_estimated_tokens=repository_tokens,
            candidate_tokens=sum(item.token_cost for item in ranked),
            selected_tokens=sum(item.token_cost for item in selected),
            token_budget=self.token_budget,
            suggested_next_investigation=next_step,
            phase=resolved_phase.value,
        )


def _select_under_budget(
    ranked: list[RankedContext], token_budget: int
) -> tuple[RankedContext, ...]:
    """Greedily select relevant context while penalizing redundancy.

    This is a deterministic Maximal Marginal Relevance (MMR) variant. The
    first item is the most relevant candidate; subsequent items balance their
    base score against similarity to already selected symbols. It prevents a
    crowded file from consuming the whole budget when another relevant file
    would add new information.
    """
    selected: list[RankedContext] = []
    remaining = token_budget
    candidates = list(ranked)
    while candidates:
        fitting = [item for item in candidates if item.token_cost <= remaining]
        if not fitting:
            break
        if not selected:
            choice = fitting[0]
        else:
            choice = max(
                fitting,
                key=lambda item: (
                    _marginal_relevance(item, selected),
                    item.score,
                    -item.token_cost,
                    item.symbol.file,
                    -item.symbol.line,
                ),
            )
        selected.append(choice)
        candidates.remove(choice)
        remaining -= choice.token_cost
    if selected or not ranked:
        return tuple(selected)
    first = ranked[0]
    empty_symbol = type(first.symbol)(**{**asdict(first.symbol), "content": ""})
    header_tokens = estimate_tokens(render_symbol(empty_symbol))
    content = truncate_to_tokens(first.symbol.content or "", token_budget - header_tokens)
    truncated_symbol = type(first.symbol)(**{**asdict(first.symbol), "content": content})
    return (
        RankedContext(
            symbol=truncated_symbol,
            semantic_relevance=first.semantic_relevance,
            dependency_relevance=first.dependency_relevance,
            test_relevance=first.test_relevance,
            risk_relevance=first.risk_relevance,
            score=first.score,
            token_cost=estimate_tokens(render_symbol(truncated_symbol)),
        ),
    )


def _marginal_relevance(item: RankedContext, selected: list[RankedContext]) -> float:
    relevance_weight = 0.80
    redundancy = max(_symbol_similarity(item.symbol, other.symbol) for other in selected)
    return relevance_weight * item.score - (1.0 - relevance_weight) * redundancy


def _symbol_similarity(left: Symbol, right: Symbol) -> float:
    if left.file == right.file:
        return 1.0
    left_parts = _identity_terms(left)
    right_parts = _identity_terms(right)
    overlap = len(left_parts & right_parts)
    union = len(left_parts | right_parts)
    lexical = overlap / union if union else 0.0
    left_dir = left.file.replace("\\", "/").rsplit("/", 1)[0]
    right_dir = right.file.replace("\\", "/").rsplit("/", 1)[0]
    directory = 0.35 if left_dir and left_dir == right_dir else 0.0
    return max(lexical, directory)


def _identity_terms(symbol: Symbol) -> set[str]:
    normalized = f"{symbol.name} {symbol.file}".replace("\\", "/").lower()
    for marker in ("/", ".", "-", "_"):
        normalized = normalized.replace(marker, " ")
    return {part for part in normalized.split() if len(part) > 1}


def estimate_repository_tokens(root: Path) -> int:
    characters = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _CODE_EXTENSIONS:
            continue
        if any(part in _IGNORED_PARTS or part.startswith(".ast-cache") for part in path.parts):
            continue
        try:
            characters += path.stat().st_size
        except OSError:
            continue
    return max(0, (characters + 3) // 4)


def _render_list(items: tuple[str, ...], empty: str) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1)) or empty
