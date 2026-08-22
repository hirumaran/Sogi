"""Repository verification-tool discovery.

Deterministically inspects repository-declared tooling (``pyproject.toml``,
``package.json``, ``Makefile``, ``Cargo.toml``) and returns the checks Sogi
should run to independently verify an agent's work.

Deliberately marker-based rather than full TOML parsing: discovery only needs
to know *that* a tool is configured, not parse its settings, which keeps the
module dependency-free and compatible with Python 3.10.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TEST = "test"
LINT = "lint"
TYPECHECK = "typecheck"
BUILD = "build"


@dataclass(frozen=True)
class DiscoveredCheck:
    """One verifiable command declared by the repository."""

    name: str
    command: str
    kind: str  # test | lint | typecheck | build

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "command": self.command, "kind": self.kind}


def discover_checks(repo_root: Path) -> tuple[DiscoveredCheck, ...]:
    """Return every check declared by the repository's own tooling."""
    root = repo_root.expanduser().resolve()
    checks: list[DiscoveredCheck] = []
    checks.extend(_from_pyproject(root))
    checks.extend(_from_pytest_ini(root))
    checks.extend(_from_package_json(root))
    checks.extend(_from_makefile(root))
    checks.extend(_from_cargo(root))
    return tuple(checks)


def _exists(root: Path, name: str) -> bool:
    return (root / name).is_file()


def _from_pyproject(root: Path) -> list[DiscoveredCheck]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    checks: list[DiscoveredCheck] = []
    has_tests = "[tool.pytest" in text or _exists(root, "pytest.ini") or (root / "tests").is_dir()
    if has_tests:
        checks.append(DiscoveredCheck("pytest", "pytest", TEST))
    if "[tool.ruff" in text or _exists(root, "ruff.toml") or _exists(root, ".ruff.toml"):
        checks.append(DiscoveredCheck("ruff", "ruff check .", LINT))
    if (
        "[tool.mypy" in text
        or _exists(root, "mypy.ini")
        or _exists(root, ".mypy.ini")
        or _exists(root, "mypy.toml")
    ):
        checks.append(DiscoveredCheck("mypy", "mypy .", TYPECHECK))
    return checks


def _from_pytest_ini(root: Path) -> list[DiscoveredCheck]:
    # pytest.ini / setup.cfg without pyproject still declare a test suite.
    if _exists(root, "pytest.ini") and not _exists(root, "pyproject.toml"):
        return [DiscoveredCheck("pytest", "pytest", TEST)]
    return []


def _from_package_json(root: Path) -> list[DiscoveredCheck]:
    path = root / "package.json"
    if not path.is_file():
        return []
    try:
        scripts = json.loads(path.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return []
    mapping = (("test", TEST), ("typecheck", TYPECHECK), ("lint", LINT), ("build", BUILD))
    checks: list[DiscoveredCheck] = []
    for script, kind in mapping:
        if isinstance(scripts.get(script), str):
            checks.append(DiscoveredCheck(f"npm {script}", f"npm run {script}", kind))
    return checks


def _from_makefile(root: Path) -> list[DiscoveredCheck]:
    path = root / "Makefile"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    targets = {
        line.split(":", 1)[0].strip()
        for line in lines
        if line and not line.startswith(("\t", " ", "#")) and ":" in line
    }
    checks: list[DiscoveredCheck] = []
    for target in ("test", "check"):
        if target in targets:
            checks.append(DiscoveredCheck(f"make {target}", f"make {target}", TEST))
            break
    return checks


def _from_cargo(root: Path) -> list[DiscoveredCheck]:
    if not _exists(root, "Cargo.toml"):
        return []
    return [DiscoveredCheck("cargo test", "cargo test", TEST)]
