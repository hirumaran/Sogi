"""Trustworthy observation: ingest agent tool events from host hooks.

Coding agents run inside hosts (Claude Code, and later others) that execute
every tool call on the agent's behalf. Host hooks fire on those calls whether
or not the agent cooperates, which makes them an observation channel the
agent cannot silently bypass — unlike self-reported ``record_event`` calls.

This module is a pure translator plus a worktree reconciler: host hook
payloads in, observations out. The CLI ``sogi hook`` command applies them to
a run. Because Bash commands can modify files without any Edit/Write tool
call, mutation-capable tools also trigger a Git-worktree reconciliation:
whatever actually changed on disk is recorded, independent of agent reports.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: Tool names that read a file without modifying it (Claude Code names).
READ_TOOLS = frozenset({"Read", "NotebookRead"})

#: Tool names known to modify files directly.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

#: Tools whose execution may mutate the repository in untracked ways.
MUTATION_CAPABLE_TOOLS = WRITE_TOOLS | {"Bash"}

STATE_DIR = "hook-state"
HEALTH_FILE = "hook-health.json"


# -- pure payload mapping -------------------------------------------------------


def map_hook_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate one host hook payload into observation dicts.

    Returns a list because one payload can imply more than one observation.
    Unrecognized tools produce an empty list: hooks must stay quiet about
    anything they do not understand rather than guess.
    """
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return []

    if tool in READ_TOOLS:
        path = tool_input.get("file_path")
        return [{"type": "file_read", "path": str(path)}] if path else []

    if tool in WRITE_TOOLS:
        path = tool_input.get("file_path")
        return [{"type": "file_modified", "path": str(path)}] if path else []

    if tool == "Bash":
        command = tool_input.get("command")
        if not command:
            return []
        response = payload.get("tool_response") or {}
        exit_code: int | None = None
        success: bool | None = None
        if isinstance(response, dict):
            raw_code = response.get("exit_code")
            if isinstance(raw_code, int):
                exit_code = raw_code
            elif response.get("is_error") is True:
                success = False
            elif response.get("is_error") is False:
                success = True
        if exit_code is not None:
            success = exit_code == 0
        observation: dict[str, Any] = {
            "type": "command_finished",
            "command": str(command),
        }
        if exit_code is not None:
            observation["exit_code"] = exit_code
        if success is not None:
            observation["success"] = success
        return [observation]

    return []


def read_payload(stream: Any = None) -> dict[str, Any] | None:
    """Parse one JSON hook payload from a stream (default: current stdin)."""
    if stream is None:
        stream = sys.stdin
    try:
        text = stream.read()
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def apply_to_service(
    service: Any,
    observations: list[dict[str, Any]],
    run_id: str,
    *,
    session_id: str | None = None,
) -> int:
    """Apply mapped observations to a run; returns how many were recorded."""
    recorded = 0
    for item in observations:
        kind = item["type"]
        try:
            if kind == "file_read":
                service.record_file_read(run_id, item["path"])
            elif kind == "file_modified":
                service.record_file_modified(run_id, item["path"])
            elif kind == "command_finished":
                service.command_finished(
                    run_id,
                    item["command"],
                    exit_code=item.get("exit_code"),
                    success=item.get("success"),
                )
            else:
                continue
        except (KeyError, ValueError):
            note_health(service.repo_root, dropped=1)
            continue
        recorded += 1
    if session_id and recorded:
        # Session binding lives in the event payloads via the service layer;
        # here we only guarantee the mapping survives for reconciliation.
        pass
    return recorded


# -- worktree reconciliation ------------------------------------------------------


def _porcelain(root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [line[3:].strip().strip('"') for line in completed.stdout.splitlines() if line.strip()]


def capture_worktree(root: Path, session_id: str) -> bool:
    """Store the pre-tool worktree listing for later reconciliation."""
    state_dir = root / ".sogi" / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {"captured_at": time.time(), "files": _porcelain(root)}
    (state_dir / f"{session_id}.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return True


def reconcile_worktree(root: Path, session_id: str) -> list[str]:
    """Return files changed since capture_worktree, then clear the snapshot."""
    snapshot_path = root / ".sogi" / STATE_DIR / f"{session_id}.json"
    if not snapshot_path.is_file():
        return []
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot_path.unlink(missing_ok=True)
        return []
    before = set(snapshot.get("files", []))
    after = set(_porcelain(root))
    snapshot_path.unlink(missing_ok=True)
    changed = sorted(after - before)
    # Paths that disappeared entirely from status were committed or reset;
    # report only additions/modifications we can attribute.
    return [path for path in changed if (root / path).exists()]


# -- observation health ------------------------------------------------------------


def note_health(root: Path, *, received: int = 0, dropped: int = 0, parse_failed: int = 0) -> None:
    """Update local observation-health counters (never leaves the machine)."""
    sogi_dir = root / ".sogi"
    sogi_dir.mkdir(parents=True, exist_ok=True)
    path = sogi_dir / HEALTH_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data["hook_events_received"] = int(data.get("hook_events_received", 0)) + received
    data["hook_events_dropped"] = int(data.get("hook_events_dropped", 0)) + dropped
    data["payload_parse_failures"] = int(data.get("payload_parse_failures", 0)) + parse_failed
    data["last_hook_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def read_health(root: Path) -> dict[str, Any]:
    path = root / ".sogi" / HEALTH_FILE
    if not path.is_file():
        return {"observed": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"observed": False}
    data["observed"] = True
    return data


# -- end-to-end hook handling ------------------------------------------------------


def process_payload(
    repo_root: Path, payload: dict[str, Any], run_id: str, session_id: str | None
) -> int:
    """Full pipeline for one hook event; returns count of recorded events."""
    event_name = payload.get("hook_event_name")
    tool = str(payload.get("tool_name", ""))
    sid = session_id or str(payload.get("session_id") or "default")

    if event_name == "PreToolUse":
        if tool in MUTATION_CAPABLE_TOOLS:
            capture_worktree(repo_root, sid)
        return 0

    # PostToolUse (and unknown events): map + reconcile.
    observations = map_hook_payload(payload)
    if tool in MUTATION_CAPABLE_TOOLS:
        for path in reconcile_worktree(repo_root, sid):
            direct = {item.get("path") for item in observations}
            if path not in direct:
                observations.append({"type": "file_modified", "path": path})
    if not observations:
        return 0

    from sogi.runs.service import RunService  # no cycle: service never imports hooks

    service = RunService(repo_root)  # analyzer loads lazily; not needed here
    try:
        return apply_to_service(service, observations, run_id)
    finally:
        service.close()
