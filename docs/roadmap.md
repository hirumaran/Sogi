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
4. `sogi run start/show/events/list/complete` and `sogi context --run` drive the lifecycle.
5. Everything persists across restarts.

## Completed: M2 — MCP control plane

1. `sogi mcp` exposes seven operations: `understand_task`, `get_context`, `get_state`,
   `record_decision`, `record_event`, `check_scope`, and `verify`.
2. The tool logic lives in a testable facade (`SogiMcp`) with a thin FastMCP adapter.
3. The `mcp` SDK is an optional extra; the rest of Sogi never depends on it.

## Completed: M3 — Governor, verifier, measurement

1. Deterministic Engineering Governor over the event stream:
   repeated-read, failure-loop, and scope-expansion detection, deduplicated
   by kind+subject and stored as `warning_raised` events.
2. Independent verification (`sogi verify <run_id>`): discovers repository-declared
   checks (pytest/ruff/mypy, npm scripts, Makefile, cargo), executes them, and maps
   evidence to acceptance criteria as `SATISFIED` / `VIOLATED` / `UNVERIFIED`.
3. Run metrics (`sogi metrics <run_id>`): exploration, commands, interventions,
   context use, verification counts, duration.
4. Claude Code integration (`sogi agent claude`): MCP config + PostToolUse hooks so
   observations do not depend on voluntary agent self-reporting; `sogi hook`
   ingests host events into the active run.
5. Completion gate: `complete()` rejects runs without verification evidence;
   failed checks block completion; unverified criteria require an explicit policy
   decision; forced bypasses record a visible intervention.

## In progress: M4 — Trustworthy loop

1. Verification staleness: a verification watermark (event sequence + worktree
   fingerprint) that invalidates evidence when the repository changes afterward.
2. Governor severity levels (INFO/WARNING/HIGH/CRITICAL) wired into the gate.
3. Session-bound hooks (explicit run/session ids instead of newest-run-wins).
4. Worktree reconciliation: derive file modifications from actual Git state after
   mutation-capable tools, not just agent reports.
5. Patch assessment: deleted/weakened tests, dependency manifest changes, risk tiers.
6. Repository-local configuration (`.sogi.toml`) and `sogi doctor`.
7. CI across supported Python versions with warnings-as-errors.

## Later phases (deliberately not started)

1. Phase-aware context compiler with progressive disclosure and MMR diversity.
2. Controlled evaluation harness (Sogi-on vs Sogi-off, identical tasks/models).
3. Second coding-agent adapter (Codex) proving agent agnosticism.
4. Event-sourced replay, fault injection, counterfactual experiments.

No performance claim should be published without controlled results.
