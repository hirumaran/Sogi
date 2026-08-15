from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_/-]{2,}")
_STOP_WORDS = {
    "and",
    "are",
    "but",
    "change",
    "does",
    "fix",
    "for",
    "from",
    "have",
    "into",
    "not",
    "that",
    "the",
    "this",
    "with",
}


@dataclass(frozen=True)
class TaskSpec:
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("Task objective cannot be empty")

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        *,
        acceptance_criteria: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> TaskSpec:
        objective = " ".join(prompt.split())
        seen: set[str] = set()
        concepts: list[str] = []
        for match in _WORD.finditer(objective.lower()):
            word = match.group(0).strip("/-")
            if word in _STOP_WORDS or word in seen:
                continue
            seen.add(word)
            concepts.append(word)
        return cls(
            objective=objective,
            acceptance_criteria=tuple(item.strip() for item in acceptance_criteria if item.strip()),
            constraints=tuple(item.strip() for item in constraints if item.strip()),
            concepts=tuple(concepts[:12]),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
