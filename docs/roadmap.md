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

1. `sogi mcp` exposes the control plane: `understand_task`, `get_context`, `localize`,
   `get_state`, `record_decision`, `record_event`, `check_scope`,
   `propose_patch`/`apply_patch`, `verify`, and `record_usage`.
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

## Completed: M5 — Patch engine, localization, dependency doctor

1. Governed patch engine (`PatchProvider` port): `sogi patch propose` computes a
   dry-run diff for a symbol replacement or an ast-grep pattern rewrite without
   touching the working tree; `sogi patch apply` is the only write path.
2. Stale-edit protection: symbol regions are content-hashed at proposal time; an
   `expected_hash` mismatch (or any drift between propose and apply) rejects the
   edit with an explicit re-localize error. Pattern rewrites are guarded by a
   worktree fingerprint captured at proposal time.
3. Scope-checked application: touched files must lie inside the task's compiled
   scope, unacknowledged violations persist HIGH audit findings and refuse to
   apply; explicit acknowledgement unblocks the same patch.
4. Auditable lifecycle: `patch_proposed` / `patch_applied` events carry the full
   request and diff so replay reproduces patches exactly (`check-integrity` covers
   them); applied files are recorded as provenance-stamped `file_modified`
   observations the governor sees like any other edit.
5. Hierarchical localization (`sogi context --format localization`, MCP `localize`):
   repository → files → symbols → exact line regions in tiers (HIGH / MEDIUM /
   RISK DEPENDENCY), with protected callers of edited symbols surfaced.
6. External dependency doctor: verifies python/git/analyzer CLI (required), MCP
   SDK/ast-grep/Semgrep/Comby (optional), Docker (research), reports versions and
   executability, checks pinned `external/revisions.json` revisions for drift,
   and external checkouts moved under `external/`.

Remaining in M4:

1. Real Claude Code end-to-end trial (hooks + MCP together) on a live session.
2. Full multi-session run binding beyond the newest-run fallback.
3. Evidence providers beyond pytest/JUnit: coverage, static analysis, type-check,
   build artifacts; manual-evidence entry with explicit provenance.
4. OS/container sandboxing for hostile repositories. The local verifier now has a
   restricted launch policy (no shell, executable validation, filtered environment,
   bounded output, process-group timeout termination), but repository test code still
   has the host user's filesystem and network privileges.

Completed within M4 since the last update:

12. Observation provenance: every file/command event carries host, session_id,
    tool_name, hook_event_name, and observation_source (`host_hook` vs
    `agent_reported`), persisted in event payloads — stored observations can
    prove where they came from.
13. Structured verification evidence: pytest commands are instrumented with JUnit
    XML reports and parsed into executed test node IDs. Criterion status is now
    driven by executed identities: a matching passing test proves SATISFIED, a
    failing one VIOLATED, a skipped one stays UNVERIFIED even when the suite
    exits green. File-level mapping remains only as a labeled fallback.

## Later phases (deliberately not started)

1. Phase-aware context compiler with progressive disclosure and MMR diversity.
2. Internal task suite for controlled experiments at scale; SWE-bench/OpenHands.
3. Second coding-agent adapter (Codex) proving agent agnosticism.
4. Fault injection and counterfactual replay.

No performance claim should be published without controlled results.
