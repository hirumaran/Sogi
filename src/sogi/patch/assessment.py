"""Deterministic patch assessment.

Inspects the working-tree diff against a base revision and classifies what
the agent actually changed — independently of what the agent claims:

- expected vs unexpected files (scope);
- added and deleted tests;
- weakened assertions, new skips/xfails (test tampering);
- dependency manifest changes;
- an overall LOW / MEDIUM / HIGH risk tier.

Every rule is a deterministic diff rule, so results are reproducible and
auditable. Ambiguous semantic questions are deliberately not answered here.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

TEST_FILE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|[^/]*_test\.py$")
MANIFESTS = ("pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "Pipfile")
SECURITY_PATHS = ("auth", "token", "secret", "credential", "permission", "crypto")
MIGRATION_PATHS = ("migration", "migrations", "schema")

_SKIP_MARKERS = ("pytest.skip", "pytest.mark.skip", "pytest.mark.xfail", "@unittest.skip")


@dataclass
class PatchAssessment:
    """The outcome of inspecting one patch."""

    changed_files: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    tests_added: list[str] = field(default_factory=list)
    tests_deleted: list[str] = field(default_factory=list)
    tests_weakened: list[str] = field(default_factory=list)
    dependency_changes: list[str] = field(default_factory=list)
    risky_paths: list[str] = field(default_factory=list)
    risk: str = "LOW"

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": self.changed_files,
            "expected_files": self.expected_files,
            "unexpected_files": self.unexpected_files,
            "tests_added": self.tests_added,
            "tests_deleted": self.tests_deleted,
            "tests_weakened": self.tests_weakened,
            "dependency_changes": self.dependency_changes,
            "risky_paths": self.risky_path_reasons(),
            "risk": self.risk,
        }

    def risky_path_reasons(self) -> list[str]:
        return [f"{path}: {reason}" for path, reason in self.risky_path_details]

    #: populated alongside risky_paths with human-readable reasons
    risky_path_details: list[tuple[str, str]] = field(default_factory=list)


def analyze_patch(
    repo_root: Path,
    *,
    base: str = "HEAD",
    expected_files: tuple[str, ...] = (),
) -> PatchAssessment:
    """Assess the working tree diff of a Git repository."""
    root = repo_root.expanduser().resolve()
    assessment = PatchAssessment()

    changed = _git(root, "diff", "--name-only", base).splitlines()
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    assessment.changed_files = sorted(
        {line.strip() for line in changed + untracked if line.strip()}
    )
    if not assessment.changed_files:
        return assessment

    # Diff review is the final scope gate, so expectation is exact-match:
    # siblings of expected files are treated as unexpected and can be
    # acknowledged explicitly through the completion policy.
    expected_set = set(expected_files)
    for path in assessment.changed_files:
        if path in expected_set or TEST_FILE.search(path):
            assessment.expected_files.append(path)
        else:
            assessment.unexpected_files.append(path)

    diff_text = _git_diff_with_content(root, base, assessment.changed_files)

    assessment.tests_added = [
        path
        for path in assessment.changed_files
        if TEST_FILE.search(path) and path in set(untracked)
    ]
    assessment.tests_deleted = _deleted_tests(root, base)

    assessment.tests_weakened = _weakened_tests(diff_text, assessment.changed_files)
    assessment.dependency_changes = [
        path for path in assessment.changed_files if Path(path).name in MANIFESTS
    ]
    assessment.risky_path_details = _risky_paths(assessment.unexpected_files)
    assessment.risk = _risk_tier(assessment)
    return assessment


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as exc:
        raise RuntimeError(f"git unavailable: {exc}") from exc
    if completed.returncode != 0:
        return ""
    return completed.stdout


def _git_diff_with_content(root: Path, base: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return _git(root, "diff", "--unified=0", base, "--", *paths[:200])


def _side_lines(diff_text: str, path: str, prefix: str) -> list[str]:
    """Collect +/- payload lines for one file's hunk section."""
    lines: list[str] = []
    in_file = False
    target = f"+++ b/{path}"
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            in_file = line == target
            continue
        if line.startswith("--- "):
            continue
        if not in_file:
            continue
        if line.startswith(prefix) and not line.startswith(prefix * 3):
            lines.append(line[1:])
    return lines


def _deleted_tests(root: Path, base: str) -> list[str]:
    output = _git(root, "diff", "--name-status", base)
    deleted: list[str] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].startswith("D") and TEST_FILE.search(parts[1]):
            deleted.append(parts[1])
    return deleted


def _weakened_tests(diff_text: str, changed: list[str]) -> list[str]:
    """Detect skips introduced or assertions removed inside test files."""
    weakened: list[str] = []
    for path in filter(lambda p: TEST_FILE.search(p), changed):
        added = _side_lines(diff_text, path, "+")
        removed = _side_lines(diff_text, path, "-")
        skip_introduced = any(marker in line for line in added for marker in _SKIP_MARKERS)
        assertions_removed = sum("assert" in line for line in removed)
        assertions_added = sum("assert" in line for line in added)
        if skip_introduced or assertions_removed > assertions_added:
            weakened.append(path)
    return weakened


def _risky_paths(unexpected: list[str]) -> list[tuple[str, str]]:
    """Classify *unexpected* files by sensitivity.

    In-scope changes to sensitive paths are the task's business; an
    out-of-scope change touching auth/secrets/CI is what needs scrutiny.
    """
    risky: list[tuple[str, str]] = []
    for path in unexpected:
        lowered = path.lower()
        if any(marker in lowered for marker in SECURITY_PATHS):
            risky.append((path, "security-sensitive path"))
        elif any(marker in lowered for marker in MIGRATION_PATHS):
            risky.append((path, "schema/migration change"))
        elif ".github" in lowered or lowered.endswith((".yml", ".yaml")):
            risky.append((path, "CI/configuration change"))
    return risky


def _risk_tier(assessment: PatchAssessment) -> str:
    if assessment.tests_deleted or assessment.tests_weakened:
        return "HIGH"
    if assessment.risky_path_details:
        return "HIGH"
    if assessment.dependency_changes or len(assessment.unexpected_files) > 3:
        return "MEDIUM"
    if assessment.unexpected_files:
        return "MEDIUM"
    return "LOW"
