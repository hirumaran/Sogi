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
   *Done with limitations:* the fingerprint is now content-sensitive (hashes
   staged/unstaged diff contents plus untracked file content hashes, not just
   filenames) and the completion gate compares `git_head` as well as `diff_hash`,
   so verify→edit-an-already-dirty-file→complete and verify→commit→complete
   are both rejected.
2. Governor severity levels (INFO/WARNING/HIGH/CRITICAL) wired into the gate.
3. Session-bound hooks (explicit run/session ids instead of newest-run-wins).
4. Worktree reconciliation: derive file modifications from actual Git state after
   mutation-capable tools, not just agent reports.
5. Patch assessment: deleted/weakened tests, dependency manifest changes, risk tiers.
   *Done:* assessment is now automatic inside `verify()` (not only via explicit
   `sogi patch`), persisted with its warnings in one transaction, deduped against
   the governor and prior assessments, and tied to the content-sensitive
   snapshot so unacknowledged HIGH/CRITICAL findings block completion.
6. Repository-local configuration (`.sogi.toml`) and `sogi doctor`.
7. CI across macOS/Linux, Python 3.10-3.13, warnings-as-errors, package build.
8. Event replay: deterministic reducer over the full event stream,
   `sogi run rebuild`, and `sogi run check-integrity` comparing projection to
   stream. Warning events carry subjects so replay matches the projection.
9. Usage/cost metrics: host/model-reported tokens and cost via `record_usage`
   (service + MCP tool), surfaced in metrics with explicit provenance —
   unreported usage stays None rather than being estimated.
10. Evaluation harness: task suites (`sogi eval run`), baseline/sogi arms with
    identical tasks/limits, shell or mock agent runners, raw JSONL results, and
    arm comparison (`sogi eval compare`). Token deltas only computed when both
    arms actually reported usage.
11. Trustworthy observation: PreToolUse/PostToolUse hooks bound to an explicit
    session id; Git-worktree reconciliation derives file modifications from
    actual repository state after mutation-capable tools (Bash included), so
    Bash-caused changes are observed without voluntary self-reporting; hook
    health counters (`hook_events_received/dropped`, `payload_parse_failures`)
    surface observation-channel problems via `check_scope`.

Remaining in M4:

1. Real Claude Code end-to-end trial (hooks + MCP together) on a live session.
2. Full multi-session run binding beyond the newest-run fallback.
3. Evidence providers richer than filename matching (test node IDs, coverage).
4. Sandboxed/restricted check execution policy.

## Later phases (deliberately not started)

1. Phase-aware context compiler with progressive disclosure and MMR diversity.
2. Internal task suite for controlled experiments at scale; SWE-bench/OpenHands.
3. Second coding-agent adapter (Codex) proving agent agnosticism.
4. Fault injection and counterfactual replay.

No performance claim should be published without controlled results.
