"""Hierarchical localization: repository → files → symbols → exact regions.

The ContextCompiler answers "what fits in the budget". The Localizer answers
the complementary question: *where exactly* should the agent look, in what
order, and what must not be broken. It narrows the repository in stages —

1. discover candidate symbols for the task (repository intelligence),
2. rank them with the same phase-aware scoring the compiler uses,
3. tier them into HIGH (edit/inspect first), MEDIUM (supporting context),
   and RISK_DEPENDENCY (callers that this change could break),

so an agent gets ``refresh_token`` plus one caller plus one test instead of
an entire directory tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sogi.core.phases import EngineeringPhase
from sogi.core.task_spec import TaskSpec
from sogi.repository.provider import RepositoryProvider, Symbol

from .ranking import RankedContext, rank_symbol

HIGH = "HIGH"
MEDIUM = "MEDIUM"
RISK_DEPENDENCY = "RISK DEPENDENCY"

#: Tier boundaries relative to the best candidate's score.
_HIGH_RATIO = 0.70
_MEDIUM_RATIO = 0.35
#: Hard caps keep the output a decision list, not another index dump.
_MAX_HIGH = 5
_MAX_PER_TIER = 8


@dataclass(frozen=True)
class LocalizedEntry:
    """One localized region with its priority tier."""

    tier: str
    symbol: Symbol
    reason: str

    @property
    def region(self) -> str:
        end = self.symbol.end_line or self.symbol.line
        return f"lines {self.symbol.line}-{end}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "region": self.region,
            "symbol": {
                "name": self.symbol.name,
                "file": self.symbol.file,
                "line": self.symbol.line,
                "end_line": self.symbol.end_line,
                "kind": self.symbol.kind,
            },
        }


@dataclass(frozen=True)
class Localization:
    objective: str
    entries: tuple[LocalizedEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def render(self) -> str:
        lines = [
            "SOGI LOCALIZATION",
            "=================",
            "",
            f"OBJECTIVE\n{self.objective}",
            "",
        ]
        current_tier = None
        for entry in self.entries:
            if entry.tier != current_tier:
                current_tier = entry.tier
                lines.append(current_tier)
            lines.append(f"  {entry.symbol.file}")
            lines.append(f"  {entry.symbol.name}")
            lines.append(f"  {entry.region} — {entry.reason}")
            lines.append("")
        if not self.entries:
            lines.append("No candidates found for this objective.")
        return "\n".join(lines).rstrip() + "\n"


class Localizer:
    """Staged file → symbol → region narrowing over repository intelligence."""

    def __init__(self, provider: RepositoryProvider) -> None:
        self.provider = provider

    def localize(
        self,
        task: TaskSpec,
        *,
        prepare: bool = True,
        phase: EngineeringPhase | str = EngineeringPhase.INVESTIGATE,
    ) -> Localization:
        resolved_phase = EngineeringPhase(phase)
        if prepare:
            self.provider.prepare()
        snapshot = self.provider.discover(task.objective)
        ranked = sorted(
            (
                rank_symbol(symbol, task.concepts, phase=resolved_phase)
                for symbol in snapshot.symbols
            ),
            key=lambda item: (-item.score, item.symbol.file, item.symbol.line),
        )
        if not ranked:
            return Localization(objective=task.objective, entries=())

        high_cut = ranked[0].score * _HIGH_RATIO
        medium_cut = ranked[0].score * _MEDIUM_RATIO
        high = [item for item in ranked if item.score >= high_cut][:_MAX_HIGH]
        selected_names = {(item.symbol.name, item.symbol.file) for item in high}

        entries = [
            LocalizedEntry(
                tier=HIGH,
                symbol=item.symbol,
                reason=_high_reason(item),
            )
            for item in high
        ]

        medium = [
            item
            for item in ranked
            if medium_cut <= item.score < high_cut
            and (item.symbol.name, item.symbol.file) not in selected_names
        ][:_MAX_PER_TIER]
        entries.extend(
            LocalizedEntry(tier=MEDIUM, symbol=item.symbol, reason="supporting context")
            for item in medium
        )
        selected_names.update((item.symbol.name, item.symbol.file) for item in medium)

        # Protected paths: callers of HIGH symbols that localization did not
        # already surface. Editing a callee without checking these is how
        # regressions happen.
        for item in high:
            for caller in self.provider.callers(item.symbol.name):
                if (caller.name, caller.file) in selected_names:
                    continue
                entries.append(
                    LocalizedEntry(
                        tier=RISK_DEPENDENCY,
                        symbol=caller,
                        reason=f"calls {item.symbol.name}; verify behavior stays intact",
                    )
                )
                selected_names.add((caller.name, caller.file))
        return Localization(objective=task.objective, entries=tuple(entries))


def _high_reason(item: RankedContext) -> str:
    if item.test_relevance > 0 and item.semantic_relevance > 0:
        return "directly related, with test coverage"
    if item.risk_relevance > 0:
        return "risk-sensitive path for this change"
    if item.dependency_relevance > 0:
        return "central to the dependency neighborhood"
    return "strongest match for the task concepts"
