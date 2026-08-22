import sys
from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.mcp.server import SogiMcp
from sogi.runs.service import RunService

try:
    import mcp  # noqa: F401
except ImportError:
    mcp = None  # type: ignore[assignment]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture()
def facade(repo: Path) -> SogiMcp:
    service = RunService(repo, provider=FakeProvider(repo))
    return SogiMcp(service)


def test_understand_task_starts_and_selects_run(facade: SogiMcp) -> None:
    summary = facade.understand_task(
        "Fix expired refresh token redirect",
        acceptance_criteria=["Redirect to /login"],
        constraints=["Preserve OAuth"],
    )

    assert summary["run_id"]
    assert summary["objective"] == "Fix expired refresh token redirect"
    assert summary["acceptance_criteria"] == ["Redirect to /login"]
    assert summary["phase"] == "investigate"
    assert facade.current_run_id == summary["run_id"]


def test_get_context_compiles_if_missing(facade: SogiMcp) -> None:
    facade.understand_task("Fix expired refresh token redirect", budget=1200)

    context = facade.get_context()

    assert context["task"]["objective"] == "Fix expired refresh token redirect"
    assert context["metrics"]["selected_tokens"] <= 1200
    assert context["context"]


def test_get_state_returns_full_record(facade: SogiMcp) -> None:
    run_id = facade.understand_task("Fix auth")["run_id"]
    facade.record_decision("Use middleware", run_id=run_id)

    state = facade.get_state()

    assert state["run_id"] == run_id
    assert state["state"]["decisions"] == ["Use middleware"]
    assert state["telemetry"]["context_compilations"] == 1


def test_record_decision_requires_active_run(facade: SogiMcp) -> None:
    with pytest.raises(ValueError):
        facade.record_decision("Use middleware")


def test_record_event_files_and_commands(facade: SogiMcp) -> None:
    run_id = facade.understand_task("Fix auth", budget=1200)["run_id"]

    facade.record_event("file_read", path="src/auth.py")
    facade.record_event("file_modified", path="src/auth.py")
    facade.record_event("command_finished", command="pytest tests/", exit_code=1, success=False)

    state = facade.get_state(run_id=run_id)
    assert state["telemetry"]["files_read"] == ["src/auth.py"]
    assert state["telemetry"]["files_modified"] == ["src/auth.py"]
    assert state["telemetry"]["commands"][0]["success"] is False


def test_record_event_rejects_unknown_type(facade: SogiMcp) -> None:
    facade.understand_task("Fix auth")
    with pytest.raises(ValueError):
        facade.record_event("file_deleted", path="src/auth.py")


def test_check_scope_reports_warnings(facade: SogiMcp) -> None:
    facade.understand_task("Fix expired refresh token redirect")

    clean = facade.check_scope()
    assert clean["clean"] is True

    for _ in range(3):
        facade.record_event("file_read", path="src/auth.py")

    scoped = facade.check_scope()
    assert scoped["clean"] is False
    assert scoped["warnings"][0]["kind"] == "repeated_read"


def test_verify_returns_report(facade: SogiMcp) -> None:
    facade.understand_task(
        "Fix auth",
        acceptance_criteria=["Auth behavior works"],
    )

    report = facade.verify()

    assert report["run_id"]
    assert report["outcome"] in {"PASS", "PASS_WITH_UNVERIFIED", "FAIL", "INCONCLUSIVE"}
    assert report["criteria"][0]["criterion"] == "Auth behavior works"


def test_explicit_run_id_overrides_current(facade: SogiMcp) -> None:
    first = facade.understand_task("Fix auth")["run_id"]
    second = facade.understand_task("Add billing")["run_id"]

    state = facade.get_state(run_id=first)

    assert state["run_id"] == first
    assert state["task"]["objective"] == "Fix auth"
    assert facade.current_run_id == second


def test_unknown_run_id_raises(facade: SogiMcp) -> None:
    with pytest.raises(ValueError):
        facade.get_state(run_id="missing")


@pytest.mark.skipif(mcp is None, reason="mcp extra not installed")
def test_stdio_server_registers_all_tools(repo: Path) -> None:
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run() -> list[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sogi", "mcp", "--repo", str(repo)],
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return sorted(tool.name for tool in tools.tools)

    names = asyncio.run(run())
    assert names == [
        "check_scope",
        "get_context",
        "get_state",
        "record_decision",
        "record_event",
        "record_usage",
        "understand_task",
        "verify",
    ]
