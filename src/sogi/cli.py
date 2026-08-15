from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sogi.context.compiler import ContextCompiler
from sogi.core.task_spec import TaskSpec
from sogi.repository.tree_sitter_provider import AnalyzerCommandError, TreeSitterProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sogi",
        description="Software-engineering control plane for coding agents",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    context = subcommands.add_parser("context", help="Compile focused repository context")
    context.add_argument("task", help="Natural-language engineering task")
    context.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root")
    context.add_argument("--budget", type=int, default=4000, help="Context token budget")
    context.add_argument("--criterion", action="append", default=[], help="Acceptance criterion")
    context.add_argument("--constraint", action="append", default=[], help="Task constraint")
    context.add_argument("--format", choices=("text", "json"), default="text")
    context.add_argument("--no-index", action="store_true", help="Use an existing analyzer index")
    context.add_argument(
        "--analyzer-command",
        help="Analyzer executable path (or set SOGI_TSA_COMMAND for a command string)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "context":
        return 2
    command = (args.analyzer_command,) if args.analyzer_command else None
    try:
        task = TaskSpec.from_prompt(
            args.task,
            acceptance_criteria=tuple(args.criterion),
            constraints=tuple(args.constraint),
        )
        provider = TreeSitterProvider(args.repo, command=command)
        compiled = ContextCompiler(provider, token_budget=args.budget).compile(
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
