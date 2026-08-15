from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .engineering_state import EngineeringState


class JsonStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def save(self, state: EngineeringState) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{state.task_id}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{state.task_id}-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, indent=2, sort_keys=True)
                handle.write("\n")
            Path(temporary).replace(target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target

    def load(self, task_id: str) -> EngineeringState:
        path = self.root / f"{task_id}.json"
        with path.open(encoding="utf-8") as handle:
            return EngineeringState.from_dict(json.load(handle))
