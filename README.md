# Sogi

Sogi is a software-engineering control plane for coding agents. It sits beside an
existing agent, selects the smallest useful repository context, preserves compact
engineering state, and will eventually govern scope and independently verify work.

Sogi is not another coding agent and it does not fork its repository-intelligence
backend. Tree-sitter Analyzer is consumed behind a replaceable `RepositoryProvider`
boundary.

## Current milestone

Sogi now persists complete engineering sessions as **runs**:

- a `RunRecord` binds `TaskSpec`, `EngineeringState`, compiled context, and telemetry;
- every observable event is appended to an append-only event log (the source of truth);
- runs persist to SQLite (`.sogi/sogi.db`) with a human-readable JSON snapshot per run;
- `sogi run start/show/events/list` and `sogi context --run` drive the lifecycle;
- an MCP server exposes `understand_task`, `get_context`, `get_state`, and
  `record_decision` so a coding agent can use Sogi as an external control plane.

## Quick start

The sibling `tree-sitter-analyzer/` checkout is used automatically during local
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

```bash
sogi mcp --repo .
```

Register it in Claude Code's MCP configuration:

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

An agent can then call the four tools in sequence:

```text
1. understand_task("Fix expired refresh-token redirect", ...)
2. get_context()
3. investigate / edit
4. record_decision("Handle expiration in refresh middleware ...")
5. get_state()
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
RunService (lifecycle)
   ↓
CLI + MCP
```

The next milestone is to add deterministic governor checks (repeated-read,
repeated-failure, scope-expansion) and an independent verification command.
See [docs/roadmap.md](docs/roadmap.md).
