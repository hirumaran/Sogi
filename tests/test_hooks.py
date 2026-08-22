"""Tests for the host-hook observation channel."""

import io
import json
import sys
from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.cli import main
from sogi.integrations import hooks as hook_ingest
from sogi.integrations.agent.claude import build_hooks_settings
from sogi.runs.service import RunService


def payload(tool_name: str, tool_input: dict, tool_response: dict | None = None) -> dict:
    body = {"hook_event_name": "PostToolUse", "tool_name": tool_name, "tool_input": tool_input}
    if tool_response is not None:
        body["tool_response"] = tool_response
    return body


# -- pure mapping ---------------------------------------------------------------


def test_read_tool_maps_to_file_read() -> None:
    assert hook_ingest.map_hook_payload(payload("Read", {"file_path": "/x/auth.py"})) == [
        {"type": "file_read", "path": "/x/auth.py"}
    ]


def test_write_tools_map_to_file_modified() -> None:
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        mapped = hook_ingest.map_hook_payload(payload(tool, {"file_path": "/x/a.py"}))
        assert mapped == [{"type": "file_modified", "path": "/x/a.py"}]


def test_bash_maps_to_command_finished_with_exit_code() -> None:
    mapped = hook_ingest.map_hook_payload(payload("Bash", {"command": "pytest"}, {"exit_code": 1}))
    assert mapped == [
        {"type": "command_finished", "command": "pytest", "exit_code": 1, "success": False}
    ]


def test_bash_is_error_without_exit_code() -> None:
    mapped = hook_ingest.map_hook_payload(
        payload("Bash", {"command": "pytest"}, {"is_error": True})
    )
    assert mapped[0]["success"] is False


def test_unknown_tool_maps_to_nothing() -> None:
    assert hook_ingest.map_hook_payload(payload("WebFetch", {"url": "https://x"})) == []


def test_malformed_inputs_are_ignored() -> None:
    assert hook_ingest.map_hook_payload({"tool_name": "Read"}) == []
    assert hook_ingest.map_hook_payload({"tool_name": "Read", "tool_input": "junk"}) == []
    assert hook_ingest.read_payload(io.StringIO("not json")) is None
    assert hook_ingest.read_payload(io.StringIO("")) is None


def test_read_payload_parses_valid_json() -> None:
    stream = io.StringIO(json.dumps(payload("Read", {"file_path": "a.py"})))
    parsed = hook_ingest.read_payload(stream)
    assert parsed is not None
    assert parsed["tool_name"] == "Read"


# -- service integration ----------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_active_run_resolution(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    assert service.active_run_id() is None

    first = service.start("Fix auth", compile_context=False).run_id

    assert service.active_run_id() == first
    assert (repo / ".sogi" / "active_run").read_text().strip() == first

    second = service.start("Add billing", compile_context=False).run_id
    assert service.active_run_id() == second


def test_active_run_falls_back_to_newest_open_run(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = service.start("Fix auth", compile_context=False).run_id
    (repo / ".sogi" / "active_run").unlink()

    assert service.active_run_id() == run_id

    service.complete(run_id, force=True)
    assert service.active_run_id() is None


def test_cli_hook_records_into_active_run(capsys, repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    service.start("Fix auth", compile_context=False)

    stdin = io.StringIO(json.dumps(payload("Bash", {"command": "pytest tests/"}, {"exit_code": 0})))
    original = sys.stdin
    sys.stdin = stdin  # type: ignore[assignment]
    try:
        code = main(["hook", "--repo", str(repo)])
    finally:
        sys.stdin = original

    assert code == 0
    run_id = service.active_run_id()
    record = service.get(run_id)
    assert record.telemetry.commands[-1].success is True


def test_cli_hook_silent_when_no_active_run(capsys, repo: Path) -> None:
    stdin = io.StringIO(json.dumps(payload("Read", {"file_path": "a.py"})))
    original = sys.stdin
    sys.stdin = stdin  # type: ignore[assignment]
    try:
        code = main(["hook", "--repo", str(repo)])
    finally:
        sys.stdin = original
    assert code == 0
    assert capsys.readouterr().out == ""


# -- launcher settings -------------------------------------------------------------


def test_hooks_settings_shape(tmp_path: Path) -> None:
    settings = build_hooks_settings(tmp_path)

    entry = settings["hooks"]["PostToolUse"][0]  # type: ignore[index]
    assert "Bash" in entry["matcher"]  # type: ignore[index]
    command = entry["hooks"][0]["command"]  # type: ignore[index]
    assert "-m sogi hook --repo" in command  # type: ignore[index]
