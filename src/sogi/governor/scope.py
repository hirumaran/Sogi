"""Scope-expansion detection.

The expected scope is derived deterministically from the run's compiled
context: the related files, their ancestor directories, and the task concepts.
A modified path outside that scope suggests the agent is touching code the
task did not ask about.

Without compiled context there is no defensible scope, so the check stays
silent rather than guessing — a false scope warning is worse than none.
"""

from __future__ import annotations

from posixpath import dirname

from sogi.core.run_record import RunRecord
from sogi.events.event import Event

from .finding import Finding


def _ancestors(path: str) -> set[str]:
    directories: set[str] = set()
    current = dirname(path)
    while current:
        directories.add(current)
        current = dirname(current)
    return directories


def expected_scope(record: RunRecord) -> tuple[set[str], set[str]]:
    """Return (in-scope files and directories, task concept terms)."""
    if record.context is None:
        return set(), set()
    in_scope: set[str] = set()
    for path in record.context.related_files:
        in_scope.add(path)
        in_scope |= _ancestors(path)
    return in_scope, {concept.lower() for concept in record.task.concepts}


def check_scope_expansion(record: RunRecord, events: list[Event]) -> list[Finding]:
    """Flag modified paths that fall outside the expected scope (once per path)."""
    in_scope, concepts = expected_scope(record)
    if not in_scope:
        return []
    flagged: set[str] = set()
    findings: list[Finding] = []
    for event in events:
        if event.type != "file_modified":
            continue
        path = str(event.payload.get("path"))
        if not path or path in in_scope or path in flagged:
            continue
        lowered = path.lower()
        if _ancestors(path) & in_scope:
            continue
        if any(concept in lowered for concept in concepts):
            continue
        flagged.add(path)
        findings.append(
            Finding(
                kind="scope_expansion",
                subject=path,
                message=f"{path} appears unrelated to the requested task.",
            )
        )
    return findings
