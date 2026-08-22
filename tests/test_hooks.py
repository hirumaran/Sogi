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


# -- worktree reconciliation (trustworthy observation) ---------------------------


def _git(repo: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "grepo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_bash_caused_file_change_is_reconciled(git_repo: Path, capsys) -> None:
    """A file changed via Bash must be observed even though no Edit tool ran."""
    service = RunService(git_repo)
    run_id = service.start("Fix auth", compile_context=False).run_id
    session = "sess001"

    # PreToolUse for a mutation-capable tool captures the clean state.
    pre_payload = {
        "hook_event_name": "PreToolUse",
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": "echo x >> src/hidden.py"},
    }
    hook_ingest.process_payload(git_repo, pre_payload, run_id, session)

    # The command actually mutates the tree outside any Edit tool.
    (git_repo / "src" / "hidden.py").write_text("y = 2\n")

    post_payload = {
        "hook_event_name": "PostToolUse",
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": "echo x >> src/hidden.py"},
        "tool_response": {"exit_code": 0},
    }
    recorded = hook_ingest.process_payload(git_repo, post_payload, run_id, session)

    assert recorded >= 1
    record = service.get(run_id)
    assert "src/hidden.py" in record.telemetry.files_modified


def test_session_snapshots_are_isolated(git_repo: Path) -> None:
    service = RunService(git_repo)
    service.start("Fix auth", compile_context=False)

    hook_ingest.capture_worktree(git_repo, "s1")
    hook_ingest.capture_worktree(git_repo, "s2")
    (git_repo / "src" / "app.py").write_text("x = 2\n")

    assert hook_ingest.reconcile_worktree(git_repo, "s1") == ["src/app.py"]
    assert hook_ingest.reconcile_worktree(git_repo, "s2") == ["src/app.py"]
    # snapshots consumed exactly once
    assert hook_ingest.reconcile_worktree(git_repo, "s1") == []


def test_reconciliation_detects_second_edit_to_already_dirty_file(git_repo: Path) -> None:
    path = git_repo / "src" / "app.py"
    path.write_text("x = 2\n")
    hook_ingest.capture_worktree(git_repo, "dirty-session")

    path.write_text("x = 3\n")

    assert hook_ingest.reconcile_worktree(git_repo, "dirty-session") == ["src/app.py"]


def test_reconciliation_detects_deleted_file(git_repo: Path) -> None:
    hook_ingest.capture_worktree(git_repo, "delete-session")

    (git_repo / "src" / "app.py").unlink()

    assert hook_ingest.reconcile_worktree(git_repo, "delete-session") == ["src/app.py"]


def test_reconciliation_ignores_unchanged_dirty_file(git_repo: Path) -> None:
    path = git_repo / "src" / "app.py"
    path.write_text("x = 2\n")
    hook_ingest.capture_worktree(git_repo, "unchanged-session")

    assert hook_ingest.reconcile_worktree(git_repo, "unchanged-session") == []


def test_session_id_cannot_escape_hook_state_directory(git_repo: Path) -> None:
    hostile = "../../outside/session"

    hook_ingest.capture_worktree(git_repo, hostile)

    state_dir = git_repo / ".sogi" / hook_ingest.STATE_DIR
    assert len(list(state_dir.glob("*.json"))) == 1
    assert not (git_repo.parent / "outside" / "session.json").exists()
    assert hook_ingest.reconcile_worktree(git_repo, hostile) == []


def test_health_counters_track_flow(tmp_path: Path) -> None:
    hook_ingest.note_health(tmp_path, received=3)
    hook_ingest.note_health(tmp_path, dropped=1, parse_failed=1)

    health = hook_ingest.read_health(tmp_path)

    assert health["observed"] is True
    assert health["hook_events_received"] == 3
    assert health["hook_events_dropped"] == 1
    assert health["payload_parse_failures"] == 1
    assert "last_hook_at" in health


def test_check_scope_surfaces_observation_health(repo: Path) -> None:
    from sogi.mcp.server import SogiMcp

    facade = SogiMcp(RunService(repo, provider=FakeProvider(repo)))
    facade.understand_task("Fix auth")
    hook_ingest.note_health(repo, received=5, dropped=2)

    scope = facade.check_scope()

    assert scope["observation_health"]["observed"] is True
    assert scope["observation_health"]["hook_events_dropped"] == 2


def test_launcher_settings_include_pre_and_post_hooks(tmp_path: Path) -> None:
    settings = build_hooks_settings(tmp_path, session_id="fixed-session")
    hooks = settings["hooks"]
    assert set(hooks.keys()) == {"PreToolUse", "PostToolUse"}  # type: ignore[union-attr]
    command = hooks["PostToolUse"][0]["hooks"][0]["command"]  # type: ignore[index]
    assert "--session fixed-session" in command  # type: ignore[index]
