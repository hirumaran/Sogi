# Sogi

Sogi is a software-engineering control plane for coding agents. It sits beside an
existing agent, selects the smallest useful repository context, preserves compact
engineering state, governs scope deterministically, and independently verifies
that finished work actually satisfies its requirements.

Sogi is not another coding agent and it does not fork its repository-intelligence
backend. Tree-sitter Analyzer is consumed behind a replaceable `RepositoryProvider`
boundary.

## Current milestone

Sogi now covers a trustworthy, closed control-plane loop:

- a `RunRecord` binds `TaskSpec`, `EngineeringState`, compiled context, and telemetry;
- every observable event is appended to an append-only event log (the source of truth),
  with `sogi run rebuild` / `check-integrity` proving the projection matches the stream;
- runs persist to SQLite (`.sogi/sogi.db`) with atomic event+projection transactions;
- **host hooks observe without trust**: `sogi agent claude` wires PreToolUse/PostToolUse
  hooks so every file read, edit, and Bash command — including files changed *by* Bash —
  is recorded from actual Git state, whether or not the agent self-reports;
- the deterministic **Engineering Governor** watches the event stream for repeated
  exploration, failure loops, and scope expansion, with severities (HIGH/CRITICAL
  findings block completion until explicitly acknowledged);
- patch assessment detects deleted/weakened tests (tampering), dependency-manifest
  changes, security-sensitive paths, and out-of-scope edits on every verification;
- the **independent verifier** discovers repository-declared checks (pytest/ruff/mypy,
  npm scripts, Makefile targets, cargo) or explicit `.sogi.toml` commands, executes
  them, and maps evidence to each acceptance criterion as SATISFIED / VIOLATED /
  UNVERIFIED — pytest runs are captured as JUnit reports, so criteria are proven by
  *executed passing test node IDs*, not filename similarity; a skipped relevant test
  stays UNVERIFIED even when the suite exits green;
- verification commands cross a restricted launch-policy boundary: no shell
  interpretation, a constrained executable set, filtered environment variables,
  bounded output capture, and process-group termination on timeout. This is not an
  OS/container sandbox—repository tests still execute repository code, so hostile
  repositories require an additional isolation layer;
- **observation provenance**: every recorded event carries host, session id, tool
  name, and source (`host_hook` vs `agent_reported`), so any stored observation can
  prove where it came from;
- a **verification watermark** (event sequence + content-sensitive worktree
  fingerprint) rejects stale `verify → edit → complete` sequences at the gate;
- **usage metrics** capture host-reported tokens/cost with explicit provenance, plus
  exploration, interventions, verification outcomes, and duration (`sogi metrics`);
- an MCP server exposes the control plane (`understand_task`, `get_context`, `localize`,
  `get_state`, `record_decision`, `record_event`, `check_scope`,
  `propose_patch`/`apply_patch`, `verify`, `record_usage`);
- a **governed patch engine** turns semantic intent into mechanical edits:
  `sogi patch propose` resolves a symbol or an ast-grep pattern into a dry-run
  diff, content-hashes the target (a changed hash is a *PATCH REJECTED —
  re-localize* stale-edit error), scope-checks touched files, and only
  `sogi patch apply` writes anything, emitting auditable `patch_proposed` /
  `patch_applied` events;
- **hierarchical localization** narrows repository → files → symbols → exact line
  regions in tiers (HIGH / MEDIUM / RISK DEPENDENCY) so agents edit one function,
  not one directory;
- a controlled-evaluation harness (`sogi eval run` / `sogi eval compare`) supports
  baseline-vs-Sogi arms over repeatable task suites with raw JSONL results;
- `sogi doctor` verifies every external dependency (versions, executability,
  pinned-revision drift) so failures are attributable to a layer.

## Quick start

The `external/tree-sitter-analyzer/` checkout is used automatically during local
development when its `.venv` exists. For a clean installation, install Sogi with
its analyzer and MCP extras:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[analyzer,mcp,dev]'
```

### Start a run

```bash
sogi run start "Fix expired refresh-token redirect" \
  --repo examples/demo_repo \
  --criterion "Expired refresh tokens redirect to /login" \
  --constraint "Do not change valid-session behavior" \
  --budget 1200
```

This creates a run, compiles focused context, and prints a summary:

```text
Run: 7d3f21

Objective:
Fix expired refresh-token redirect

Phase:
INVESTIGATE

Acceptance criteria:
1

Context budget:
1200

Context selected:
243 tokens
```

### Inspect a run

```bash
sogi run show 7d3f21        # full engineering state
sogi run events 7d3f21      # append-only event log
sogi run list               # all runs
sogi context --run 7d3f21   # compile/refresh context for a run
```

Use `--format json` on any command for machine-readable output. Everything
persists across restarts under the repository root:

```text
.sogi/
├── sogi.db      # SQLite: runs table (JSON payload) + events table
└── runs/        # human-readable JSON snapshot per run
```

### Use Sogi from Claude Code (MCP)

The quickest path is the built-in launcher, which generates the MCP config and
starts Claude Code with Sogi already attached:

```bash
sogi agent claude --repo .
```

To register Sogi manually, run the server:

```bash
sogi mcp --repo .
```

and add it to Claude Code's MCP configuration:

```json
{
  "mcpServers": {
    "sogi": {
      "command": "sogi",
      "args": ["mcp", "--repo", "."]
    }
  }
}
```

An agent can then call the tools in sequence:

```text
1. understand_task("Fix expired refresh-token redirect", ...)
2. get_context()
3. investigate / edit
4. record_event("file_read", path="src/auth/refresh.py")
5. record_decision("Handle expiration in refresh middleware ...")
6. check_scope()      # any governor warnings?
7. verify()           # independent evidence against acceptance criteria
```

### Supervision and verification

While an agent works, Sogi watches deterministically:

```bash
sogi run show 7d3f21   # warnings appear in telemetry as they are raised
```

When the agent claims completion, verify independently:

```bash
sogi verify 7d3f21     # runs discovered checks, maps evidence to criteria
```

```text
VERIFICATION FAIL_WITH... example:

VERIFICATION PASS_WITH_UNVERIFIED
Run: 7d3f21

CHECKS
  [x] pytest: pytest (exit 0)
  [x] ruff: ruff check . (exit 0)

ACCEPTANCE CRITERIA
  [x] SATISFIED: Expired refresh tokens redirect to /login
        evidence: tests/test_refresh.py
  [?] UNVERIFIED: Valid-session behavior remains unchanged
        note: Relevant test exists but was not executed.
```

### Metrics

```bash
sogi metrics 7d3f21
```

```text
METRICS run 7d3f21  phase=DONE

  Files read: 5 (unique 2, repeat 3)
  Files modified: 1  Commands: 4 (failed 1)
  Sogi interventions: 2
    repeated_read: 1
    scope_expansion: 1
  Context: 1 compilations, last 243/1200 tokens
  Verification: 1 satisfied, 0 violated, 1 unverified
```

## Design boundary

```text
TaskSpec + EngineeringState + TokenBudget
                    |
                    v
             ContextCompiler
                    |
                    v
          RepositoryProvider (port)
                    |
                    v
          TreeSitterProvider (adapter)
```

Runs add a control-plane layer on top:

```text
events (append-only)
   ↓
RunRecord state
   ↓
Governor (deterministic checks)
   ↓
Verifier (independent evidence)
   ↓
Metrics (measurement)
   ↓
RunService (lifecycle)
   ↓
CLI + MCP + agent launchers
```

The next milestone is a controlled evaluation: the same model and agent, with
and without Sogi, on identical repository tasks — measuring success, token use,
interventions, and regressions before claiming any improvement.
See [docs/roadmap.md](docs/roadmap.md).
The isolated upstream research checkouts and their integration order are documented in
[docs/external-repositories.md](docs/external-repositories.md).
