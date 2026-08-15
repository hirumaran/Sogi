"""Sogi MCP server.

Exposes exactly four operations to a coding agent:

- ``understand_task``  start a run and understand the task
- ``get_context``      return the run's compiled repository context
- ``get_state``        return the run's full engineering state
- ``record_decision``  record a decision in the run

The tool logic lives in :class:`SogiMcp`, which is testable without the ``mcp``
SDK. The FastMCP wiring is a thin adapter over it and is imported lazily so the
rest of Sogi never depends on the optional ``mcp`` extra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sogi.core.run_record import RunRecord
from sogi.runs.service import RunNotFoundError, RunService


class SogiMcp:
    """MCP-facing facade over :class:`RunService`.

    A server tracks a "current run" so an agent can call the tools in sequence
    without threading a run_id through every call. Every tool also accepts an
    explicit ``run_id`` for multi-run workflows.
    """

    def __init__(self, service: RunService, *, run_id: str | None = None) -> None:
        self.service = service
        self.current_run_id = run_id

    def understand_task(
        self,
        objective: str,
        acceptance_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        budget: int = 4000,
    ) -> dict[str, Any]:
        """Start a new run for the objective and make it the current run."""
        record = self.service.start(
            objective,
            acceptance_criteria=tuple(acceptance_criteria or ()),
            constraints=tuple(constraints or ()),
            budget=budget,
        )
        self.current_run_id = record.run_id
        return _run_summary(record)

    def get_context(self, run_id: str | None = None) -> dict[str, Any]:
        """Return the run's compiled context, compiling it if necessary."""
        rid = self._resolve_run(run_id)
        record = self.service.get(rid)
        if record.context is None:
            self.service.compile_context(rid)
            record = self.service.get(rid)
        return record.context.to_dict() if record.context else {"context": None}

    def get_state(self, run_id: str | None = None) -> dict[str, Any]:
        """Return the run's full engineering state."""
        rid = self._resolve_run(run_id)
        return self.service.get(rid).to_dict()

    def record_decision(self, decision: str, run_id: str | None = None) -> dict[str, Any]:
        """Record a decision made during the run."""
        rid = self._resolve_run(run_id)
        self.service.record_decision(rid, decision)
        return {"run_id": rid, "recorded": True, "decision": decision}

    def _resolve_run(self, run_id: str | None) -> str:
        rid = run_id or self.current_run_id
        if rid is None:
            raise ValueError(
                "No run is active. Call understand_task first, or pass an explicit run_id."
            )
        try:
            self.service.get(rid)
        except RunNotFoundError as exc:
            raise ValueError(f"Unknown run: {rid}") from exc
        return rid


def _run_summary(record: RunRecord) -> dict[str, Any]:
    selected = record.telemetry.context_tokens[-1] if record.telemetry.context_tokens else None
    return {
        "run_id": record.run_id,
        "objective": record.task.objective,
        "phase": record.state.phase.value,
        "acceptance_criteria": list(record.task.acceptance_criteria),
        "constraints": list(record.task.constraints),
        "context_budget": record.telemetry.context_budget,
        "context_selected_tokens": selected,
    }


def build_server(service: RunService, *, run_id: str | None = None) -> Any:
    """Build the FastMCP server. Requires the optional ``mcp`` extra."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised via CLI
        raise ImportError(
            "The Sogi MCP server requires the 'mcp' extra: "
            "install with `pip install 'sogi[mcp]'`"
        ) from exc

    mcp = FastMCP("sogi")
    facade = SogiMcp(service, run_id=run_id)

    @mcp.tool()
    def understand_task(
        objective: str,
        acceptance_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        budget: int = 4000,
    ) -> dict[str, Any]:
        """Start a new Sogi run for an engineering objective and make it the current run.

        Args:
            objective: The engineering task, e.g. "Fix expired refresh-token redirect".
            acceptance_criteria: Optional list of testable acceptance criteria.
            constraints: Optional list of constraints the work must respect.
            budget: Context token budget for the run.
        """
        return facade.understand_task(objective, acceptance_criteria, constraints, budget)

    @mcp.tool()
    def get_context(run_id: str | None = None) -> dict[str, Any]:
        """Return the current run's compiled repository context.

        Args:
            run_id: Optional run id. Defaults to the current run.
        """
        return facade.get_context(run_id)

    @mcp.tool()
    def get_state(run_id: str | None = None) -> dict[str, Any]:
        """Return the current run's full engineering state.

        Args:
            run_id: Optional run id. Defaults to the current run.
        """
        return facade.get_state(run_id)

    @mcp.tool()
    def record_decision(decision: str, run_id: str | None = None) -> dict[str, Any]:
        """Record a decision made during the current run.

        Args:
            decision: The decision, including the reasoning, e.g.
                "Handle expiration in refresh middleware rather than validate_token
                because OAuth shares validate_token."
            run_id: Optional run id. Defaults to the current run.
        """
        return facade.record_decision(decision, run_id)

    return mcp


def main(args: argparse.Namespace) -> int:
    try:
        service = RunService(args.repo, analyzer_command=_analyzer_command(args))
    except ValueError as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    try:
        server = build_server(service, run_id=args.run)
    except ImportError as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    server.run()
    return 0


def _analyzer_command(args: argparse.Namespace) -> tuple[str, ...] | None:
    return (args.analyzer_command,) if getattr(args, "analyzer_command", None) else None


def _entrypoint() -> int:
    parser = argparse.ArgumentParser(prog="sogi mcp", description="Run the Sogi MCP server")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run")
    parser.add_argument("--analyzer-command")
    return main(parser.parse_args())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_entrypoint())
