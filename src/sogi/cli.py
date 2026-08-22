from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sogi.context.compiler import ContextCompiler
from sogi.core.task_spec import TaskSpec
from sogi.repository.tree_sitter_provider import AnalyzerCommandError, TreeSitterProvider
from sogi.runs.render import render_events
from sogi.runs.service import RunNotFoundError, RunService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sogi",
        description="Software-engineering control plane for coding agents",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    context = subcommands.add_parser("context", help="Compile focused repository context")
    context.add_argument("task", nargs="?", help="Natural-language engineering task")
    context.add_argument("--run", help="Compile context for an existing run")
    context.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    context.add_argument("--budget", type=int, default=None, help="Context token budget")
    context.add_argument("--criterion", action="append", default=[], help="Acceptance criterion")
    context.add_argument("--constraint", action="append", default=[], help="Task constraint")
    context.add_argument("--format", choices=("text", "json"), default="text")
    context.add_argument("--no-index", action="store_true", help="Use an existing analyzer index")
    context.add_argument(
        "--analyzer-command",
        help="Analyzer executable path (or set SOGI_TSA_COMMAND for a command string)",
    )

    run = subcommands.add_parser("run", help="Manage Sogi runs")
    run_sub = run.add_subparsers(dest="run_command", required=True)

    start = run_sub.add_parser("start", help="Start a new run")
    start.add_argument("objective", help="Natural-language engineering task")
    start.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    start.add_argument("--criterion", action="append", default=[], help="Acceptance criterion")
    start.add_argument("--constraint", action="append", default=[], help="Task constraint")
    start.add_argument("--budget", type=int, default=4000, help="Context token budget")
    start.add_argument("--no-context", action="store_true", help="Do not compile context at start")
    start.add_argument("--format", choices=("text", "json"), default="text")
    start.add_argument(
        "--analyzer-command",
        help="Analyzer executable path (or set SOGI_TSA_COMMAND for a command string)",
    )

    show = run_sub.add_parser("show", help="Show a run's engineering state")
    show.add_argument("run_id")
    show.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    show.add_argument("--format", choices=("text", "json"), default="text")

    events = run_sub.add_parser("events", help="Show a run's append-only event log")
    events.add_argument("run_id")
    events.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    events.add_argument("--format", choices=("text", "json"), default="text")

    list_runs = run_sub.add_parser("list", help="List runs")
    list_runs.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    list_runs.add_argument("--format", choices=("text", "json"), default="text")

    complete = run_sub.add_parser("complete", help="Complete a run through the verification gate")
    complete.add_argument("run_id")
    complete.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    complete.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Accept unverified acceptance criteria explicitly",
    )
    complete.add_argument(
        "--force",
        action="store_true",
        help="Bypass the gate (records a visible completion_forced intervention)",
    )

    verify = subcommands.add_parser("verify", help="Independently verify a run's work")
    verify.add_argument("run_id")
    verify.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    verify.add_argument("--timeout", type=float, default=600.0, help="Per-check timeout (s)")
    verify.add_argument("--format", choices=("text", "json"), default="text")

    metrics = subcommands.add_parser("metrics", help="Show a run's efficiency metrics")
    metrics.add_argument("run_id")
    metrics.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    metrics.add_argument("--format", choices=("text", "json"), default="text")

    agent = subcommands.add_parser("agent", help="Launch a coding agent supervised by Sogi")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    claude = agent_sub.add_parser("claude", help="Launch Claude Code with Sogi's MCP server")
    claude.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    claude.add_argument(
        "--analyzer-command",
        help="Analyzer executable path (or set SOGI_TSA_COMMAND for a command string)",
    )
    claude.add_argument(
        "--print-config",
        action="store_true",
        help="Print the generated MCP config and exit without launching",
    )
    claude.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Extra arguments passed through to the claude executable",
    )

    hook = subcommands.add_parser(
        "hook", help="Ingest one agent-host hook event from stdin (silent no-op on error)"
    )
    hook.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    hook.add_argument("--run", help="Attach the observation to an explicit run")

    mcp = subcommands.add_parser("mcp", help="Run the Sogi MCP server over stdio")
    mcp.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    mcp.add_argument("--run", help="Attach the server to an existing run")
    mcp.add_argument(
        "--analyzer-command",
        help="Analyzer executable path (or set SOGI_TSA_COMMAND for a command string)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "context":
        return _cmd_context(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "metrics":
        return _cmd_metrics(args)
    if args.command == "agent":
        return _cmd_agent(args)
    if args.command == "hook":
        return _cmd_hook(args)
    if args.command == "mcp":
        return _cmd_mcp(args)
    return 2


def _cmd_hook(args: argparse.Namespace) -> int:
    """Ingest a host hook payload. Hooks must never disturb the agent, so
    every failure mode exits 0 silently; only --debug surfaces problems."""
    from sogi.integrations import hooks as hook_ingest

    payload = hook_ingest.read_payload()
    if payload is None:
        return 0
    observations = hook_ingest.map_hook_payload(payload)
    if not observations:
        return 0
    try:
        with RunService(args.repo) as service:  # analyzer loads lazily; not needed here
            run_id = args.run or service.active_run_id()
            if run_id:
                hook_ingest.apply_to_service(service, observations, run_id)
    except Exception:
        if getattr(args, "debug", False):
            raise
        # A supervision failure must never break the supervised agent.
        pass
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    if args.run:
        try:
            with RunService(args.repo, analyzer_command=_analyzer_command(args)) as service:
                compiled = service.compile_context(
                    args.run, budget=args.budget, prepare=not args.no_index
                )
        except (RunNotFoundError, AnalyzerCommandError, OSError, ValueError) as exc:
            print(f"sogi: {exc}", file=sys.stderr)
            return 1
    else:
        if not args.task:
            print("sogi: context requires a task or --run", file=sys.stderr)
            return 2
        try:
            task = TaskSpec.from_prompt(
                args.task,
                acceptance_criteria=tuple(args.criterion),
                constraints=tuple(args.constraint),
            )
            provider = TreeSitterProvider(args.repo, command=_analyzer_command(args))
            compiled = ContextCompiler(provider, token_budget=args.budget or 4000).compile(
                task, prepare=not args.no_index
            )
        except (AnalyzerCommandError, OSError, ValueError) as exc:
            print(f"sogi: {exc}", file=sys.stderr)
            return 1
    if args.format == "json":
        print(json.dumps(compiled.to_dict(), indent=2, sort_keys=True))
    else:
        print(compiled.render())
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.run_command == "start":
        return _cmd_run_start(args)
    if args.run_command == "show":
        return _cmd_run_show(args)
    if args.run_command == "events":
        return _cmd_run_events(args)
    if args.run_command == "list":
        return _cmd_run_list(args)
    if args.run_command == "complete":
        return _cmd_run_complete(args)
    return 2


def _cmd_run_complete(args: argparse.Namespace) -> int:
    from sogi.runs.service import CompletionGateError

    try:
        with RunService(args.repo) as service:
            record = service.complete(
                args.run_id, allow_unverified=args.allow_unverified, force=args.force
            )
    except (RunNotFoundError, CompletionGateError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    outcome = record.telemetry.outcome or "completed"
    print(f"Run {record.run_id} complete ({outcome}).")
    return 0


def _cmd_run_start(args: argparse.Namespace) -> int:
    try:
        with RunService(args.repo, analyzer_command=_analyzer_command(args)) as service:
            record = service.start(
                args.objective,
                acceptance_criteria=tuple(args.criterion),
                constraints=tuple(args.constraint),
                budget=args.budget,
                compile_context=not args.no_context,
            )
            rendered = service.render_start(record.run_id)
    except (AnalyzerCommandError, OSError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    else:
        print(rendered)
    return 0


def _cmd_run_show(args: argparse.Namespace) -> int:
    try:
        with RunService(args.repo) as service:
            record = service.get(args.run_id)
            rendered = service.render(record.run_id)
    except (RunNotFoundError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    else:
        print(rendered)
    return 0


def _cmd_run_events(args: argparse.Namespace) -> int:
    try:
        with RunService(args.repo) as service:
            service.get(args.run_id)  # raises RunNotFoundError for unknown runs
            events = service.events.for_run(args.run_id)
    except (RunNotFoundError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps([event.to_dict() for event in events], indent=2, sort_keys=True))
    else:
        print(render_events(events))
    return 0


def _cmd_run_list(args: argparse.Namespace) -> int:
    try:
        with RunService(args.repo) as service:
            records = service.db.list_runs()
    except ValueError as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps([record.to_dict() for record in records], indent=2, sort_keys=True))
    else:
        if not records:
            print("No runs yet.")
            return 0
        lines = ["SOGI RUNS", "=========", ""]
        for record in records:
            lines.append(
                f"{record.run_id}  {record.state.phase.value.upper():<12}  {record.task.objective}"
            )
        print("\n".join(lines))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        with RunService(args.repo) as service:
            report = service.verify(args.run_id, timeout=args.timeout)
    except (RunNotFoundError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.render())
    return 0 if report.outcome.startswith("PASS") else 1


def _cmd_metrics(args: argparse.Namespace) -> int:
    from sogi.telemetry.metrics import RunMetrics

    try:
        with RunService(args.repo) as service:
            record = service.get(args.run_id)
    except (RunNotFoundError, ValueError) as exc:
        print(f"sogi: {exc}", file=sys.stderr)
        return 1
    metrics = RunMetrics.from_record(record)
    if args.format == "json":
        print(json.dumps(metrics.to_dict(), indent=2, sort_keys=True))
    else:
        print(metrics.render())
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    from sogi.integrations.agent.claude import launch

    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        print(f"sogi: Repository does not exist: {repo}", file=sys.stderr)
        return 1
    return launch(
        repo,
        args.args,
        analyzer_command=_analyzer_command(args),
        print_config=args.print_config,
    )


def _cmd_mcp(args: argparse.Namespace) -> int:
    from sogi.mcp.server import main as mcp_main

    return mcp_main(args)


def _analyzer_command(args: argparse.Namespace) -> tuple[str, ...] | None:
    return (args.analyzer_command,) if getattr(args, "analyzer_command", None) else None
