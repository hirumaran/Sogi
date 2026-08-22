"""Trustworthy observation: ingest agent tool events from host hooks.

Coding agents run inside hosts (Claude Code, and later others) that execute
every tool call on the agent's behalf. Host hooks fire on those calls whether
or not the agent cooperates, which makes them an observation channel the
agent cannot silently bypass — unlike self-reported ``record_event`` calls.

This module is a pure translator: host hook payloads in, observation tuples
out. The CLI ``sogi hook`` command applies them to a run.
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: Tool names that read a file without modifying it (Claude Code names).
READ_TOOLS = frozenset({"Read", "NotebookRead"})

#: Tool names that modify files.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


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


def apply_to_service(service: Any, observations: list[dict[str, Any]], run_id: str) -> int:
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
            continue
        recorded += 1
    return recorded
