# Sogi roadmap

## Completed foundation

1. Validate Tree-sitter Analyzer instead of assuming its capabilities.
2. Establish Sogi as an independent Python package.
3. Define the repository-provider boundary.
4. Compile ranked repository context under a fixed token budget.
5. Expose the vertical slice through `sogi context`.

## Completed: M1 — Runs

1. `RunRecord` binds `TaskSpec`, `EngineeringState`, compiled context, and telemetry.
2. Append-only event log underneath `RunRecord` (events are the source of truth).
3. SQLite persistence (`.sogi/sogi.db`) plus a human-readable JSON snapshot per run.
4. `sogi run start/show/events/list` and `sogi context --run` drive the lifecycle.
5. Everything persists across restarts.

## Completed: M2 — MCP

1. `sogi mcp` exposes exactly four operations: `understand_task`, `get_context`,
   `get_state`, and `record_decision`.
2. The tool logic lives in a testable facade (`SogiMcp`) with a thin FastMCP adapter.
3. The `mcp` SDK is an optional extra; the rest of Sogi never depends on it.

## Next

1. Add deterministic governor checks: repeated-read, repeated-failure, and
   scope-expansion detection, each stored as a `warning_raised` event in the run.
2. Add an independent verification command that runs repository-declared checks
   and maps evidence back to acceptance criteria with non-binary statuses
   (`SATISFIED` / `VIOLATED` / `UNVERIFIED`).
3. Integrate one coding agent only after the control-plane API is stable.

Benchmarking, multi-agent support, UI work, and fault injection intentionally remain
later phases. No performance claim should be published without controlled results.
