"""Repeated-read detection.

Reading the same file many times without modifying it (or anything) in
between is the classic wandering signal: the agent is re-deriving information
it already discovered instead of consulting engineering state.
"""

from __future__ import annotations

from collections import Counter

from sogi.events.event import Event

from .finding import KIND_SEVERITY, Finding


def check_repeated_reads(events: list[Event], *, threshold: int = 3) -> list[Finding]:
    """Flag paths read ``threshold`` times with no modification in between.

    A ``file_modified`` event for a path resets that path's counter: rereading
    after a change is legitimate. The finding fires exactly once per crossing,
    so a fifth read does not produce a second finding.
    """
    counts: Counter[str] = Counter()
    findings: list[Finding] = []
    for event in events:
        if event.type == "file_modified":
            counts.pop(str(event.payload.get("path")), None)
        elif event.type == "file_read":
            path = str(event.payload.get("path"))
            if not path:
                continue
            counts[path] += 1
            if counts[path] == threshold:
                findings.append(
                    Finding(
                        kind="repeated_read",
                        subject=path,
                        message=(
                            f"{path} has been read {threshold} times without "
                            "meaningful new evidence."
                        ),
                        severity=KIND_SEVERITY["repeated_read"],
                    )
                )
    return findings
