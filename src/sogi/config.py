"""Repository-local configuration (``.sogi.toml``).

Configuration keeps policy in the repository where the work happens instead
of forcing every setting through CLI flags. Missing file means defaults;
unknown keys are ignored so older Sogi versions keep working with newer
config files.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = ".sogi.toml"


def _parse_toml(text: str) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(text)
    try:
        import tomli

        return tomli.loads(text)
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            f"Parsing {CONFIG_FILE} on Python < 3.11 requires 'tomli'. "
            "Install sogi with the [config] extra."
        ) from exc


@dataclass
class SogiConfig:
    """Resolved repository configuration."""

    context_budget: int | None = None
    verification_commands: tuple[str, ...] = ()
    block_on_unverified: bool = True
    host: str = "claude-code"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, repo_root: Path) -> SogiConfig:
        path = repo_root.expanduser().resolve() / CONFIG_FILE
        if not path.is_file():
            return cls()
        try:
            data = _parse_toml(path.read_text(encoding="utf-8"))
        except (OSError, RuntimeError):
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SogiConfig:
        context = data.get("context") or {}
        verification = data.get("verification") or {}
        completion = data.get("completion") or {}
        observation = data.get("observation") or {}
        commands = verification.get("commands", [])
        return cls(
            context_budget=(
                int(context["budget"]) if isinstance(context.get("budget"), (int, float)) else None
            ),
            verification_commands=tuple(
                str(command) for command in commands if isinstance(command, str)
            ),
            block_on_unverified=bool(completion.get("block_on_unverified", True)),
            host=str(observation.get("host", "claude-code")),
            extra=data,
        )
