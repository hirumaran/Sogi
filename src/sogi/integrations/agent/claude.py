"""Claude Code integration: launch Claude supervised by Sogi's MCP server.

``sogi agent claude [extra args...]`` writes a temporary MCP config pointing
Claude Code at Sogi's stdio MCP server and execs ``claude --mcp-config ...``.
The user then talks to Claude normally; Sogi's tools (understand_task,
get_context, get_state, record_decision, record_event, check_scope, verify)
are available to the agent in the same session.

The adapter is intentionally thin — it contains no Sogi intelligence, just the
launch bridge. Future adapters (codex, gemini) should follow this shape.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def build_mcp_config(
    repo_root: Path,
    *,
    python: str | None = None,
    analyzer_command: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Return a Claude Code ``--mcp-config`` document for Sogi's server."""
    executable = python or sys.executable
    args = ["-m", "sogi", "mcp", "--repo", str(repo_root)]
    if analyzer_command:
        args.extend(["--analyzer-command", analyzer_command[0]])
    return {
        "mcpServers": {
            "sogi": {
                "command": executable,
                "args": args,
                "env": {},
            }
        }
    }


#: Tools whose execution Sogi observes through PostToolUse hooks. This is the
#: trustworthy channel: it fires for every tool call whether or not the agent
#: reports it, so governor checks and metrics rest on observed behavior.
_OBSERVED_TOOLS = "Read|NotebookRead|Edit|Write|MultiEdit|NotebookEdit|Bash"


def build_hooks_settings(
    repo_root: Path,
    *,
    python: str | None = None,
    session_id: str | None = None,
) -> dict[str, object]:
    """Return a Claude Code settings document with Sogi observation hooks.

    The hook command ingests each PostToolUse event into the active run and
    never fails loudly: a supervision problem must not break the agent. A
    session id binds observations to this launch so concurrent sessions in
    the same repository do not cross-contaminate runs.
    """
    import shlex
    import uuid

    executable = python or sys.executable
    sid = session_id or uuid.uuid4().hex[:12]
    command_parts = [
        executable,
        "-m",
        "sogi",
        "hook",
        "--repo",
        str(repo_root),
        "--session",
        sid,
    ]
    command = shlex.join(command_parts)
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": _OBSERVED_TOOLS,
                    "hooks": [{"type": "command", "command": command}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": _OBSERVED_TOOLS,
                    "hooks": [{"type": "command", "command": command}],
                }
            ],
        }
    }


def launch(
    repo_root: Path,
    extra_args: list[str],
    *,
    analyzer_command: tuple[str, ...] | None = None,
    print_config: bool = False,
) -> int:
    """Write the MCP config + hook settings and replace this process with ``claude``."""
    config = build_mcp_config(repo_root, analyzer_command=analyzer_command)
    settings = build_hooks_settings(repo_root)
    if print_config:
        print(json.dumps({"mcp_config": config, "settings": settings}, indent=2, sort_keys=True))
        return 0

    sogi_dir = repo_root / ".sogi"
    sogi_dir.mkdir(parents=True, exist_ok=True)
    config_path = sogi_dir / "claude-mcp.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    settings_path = sogi_dir / "claude-settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    try:
        os.execvp(
            "claude",
            [
                "claude",
                "--mcp-config",
                str(config_path),
                "--settings",
                str(settings_path),
                *extra_args,
            ],
        )
    except FileNotFoundError:
        print(
            "sogi: 'claude' executable not found on PATH. "
            "Install Claude Code first (https://claude.com/claude-code).",
            file=sys.stderr,
        )
        return 1
