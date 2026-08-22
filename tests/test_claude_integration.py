"""Tests for the Claude Code launch adapter."""

import json
import sys
from pathlib import Path

from sogi.cli import main
from sogi.integrations.agent.claude import build_mcp_config


def test_build_mcp_config_shape(tmp_path: Path) -> None:
    config = build_mcp_config(tmp_path)

    server = config["mcpServers"]["sogi"]  # type: ignore[index]
    assert server["args"] == ["-m", "sogi", "mcp", "--repo", str(tmp_path)]  # type: ignore[index]
    assert server["command"] == sys.executable  # type: ignore[index]


def test_build_mcp_config_with_analyzer(tmp_path: Path) -> None:
    config = build_mcp_config(tmp_path, analyzer_command=("tsa-analyze",))

    server = config["mcpServers"]["sogi"]  # type: ignore[index]
    assert "--analyzer-command" in server["args"]  # type: ignore[index]
    assert "tsa-analyze" in server["args"]  # type: ignore[index]


def test_cli_agent_print_config(capsys, tmp_path: Path) -> None:
    code = main(["agent", "claude", "--repo", str(tmp_path), "--print-config"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "sogi" in payload["mcpServers"]
    assert not (tmp_path / ".sogi").exists()  # nothing written in print mode


def test_cli_agent_missing_repo(tmp_path: Path, capsys) -> None:
    code = main(["agent", "claude", "--repo", str(tmp_path / "missing")])

    assert code == 1
    assert "does not exist" in capsys.readouterr().err
