"""Failure-loop detection.

Re-running the same failing command without changing the implementation means
the agent is retrying instead of revising its hypothesis. Any file modification
resets the loop: failing after a change is a new experiment, not a retry.
"""

from __future__ import annotations

from collections import Counter

from sogi.events.event import Event

from .finding import Finding


def check_failure_loops(events: list[Event], *, threshold: int = 3) -> list[Finding]:
    """Flag commands that failed ``threshold`` times consecutively.

    The failure streak for a command resets when the command succeeds or when
    any ``file_modified`` event occurs (a meaningful implementation change).
    Like repeated reads, the finding fires once per threshold crossing.
    """
    streaks: Counter[str] = Counter()
    findings: list[Finding] = []
    for event in events:
        if event.type == "file_modified":
            streaks.clear()
        elif event.type == "command_finished":
            command = str(event.payload.get("command"))
            if not command:
                continue
            if event.payload.get("success") is True:
                streaks.pop(command, None)
                continue
            if event.payload.get("success") is not False:
                continue
            streaks[command] += 1
            if streaks[command] == threshold:
                findings.append(
                    Finding(
                        kind="failure_loop",
                        subject=command,
                        message=(
                            f"'{command}' has failed {threshold} times without a "
                            "meaningful implementation change. Reconsider the "
                            "current hypothesis."
                        ),
                    )
                )
    return findings
