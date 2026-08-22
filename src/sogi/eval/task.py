"""Evaluation task definition.

A suite is a JSON file: {"tasks": [ ...task dicts... ]}. Tasks must be
repeatable (fixed repository revision, fixed prompt) so arms differ only in
whether Sogi supervises the agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalTask:
    """One controlled, repeatable engineering task."""

    task_id: str
    repo: str  # path or URL; resolved by the runner environment
    base_commit: str | None
    prompt: str
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvalTask:
        return cls(
            task_id=str(payload["task_id"]),
            repo=str(payload["repo"]),
            base_commit=payload.get("base_commit"),
            prompt=str(payload["prompt"]),
            acceptance_criteria=tuple(payload.get("acceptance_criteria", ())),
            constraints=tuple(payload.get("constraints", ())),
            metadata=dict(payload.get("metadata", {})),
        )


def load_suite(path: Path) -> list[EvalTask]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = [EvalTask.from_dict(item) for item in data.get("tasks", [])]
    if not tasks:
        raise ValueError(f"Suite {path} contains no tasks")
    ids = [task.task_id for task in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Suite {path} has duplicate task_ids")
    return tasks
