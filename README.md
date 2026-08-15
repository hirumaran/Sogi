# Sogi

Sogi is a software-engineering control plane for coding agents. It sits beside an
existing agent, selects the smallest useful repository context, preserves compact
engineering state, and will eventually govern scope and independently verify work.

Sogi is not another coding agent and it does not fork its repository-intelligence
backend. Tree-sitter Analyzer is consumed behind a replaceable `RepositoryProvider`
boundary.

## Current milestone

This repository contains the first vertical slice:

- deterministic `TaskSpec` and engineering phases;
- persistent JSON `EngineeringState`;
- an abstract repository provider;
- a Tree-sitter Analyzer CLI adapter;
- weighted context ranking and a hard token budget;
- a usable `sogi context` command;
- a tiny end-to-end fixture and focused tests.

## Quick start

The sibling `tree-sitter-analyzer/` checkout is used automatically during local
development when its `.venv` exists. For a clean installation, install Sogi with
its analyzer extra:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[analyzer,dev]'
```

Compile context for a repository:

```bash
sogi context "Fix expired refresh-token redirect" \
  --repo examples/demo_repo \
  --criterion "Expired refresh tokens redirect to /login" \
  --constraint "Do not change valid-session behavior" \
  --budget 1200
```

Use `--format json` for machine-readable output. Sogi incrementally prepares the
analyzer index by default; pass `--no-index` only when a valid index already exists.

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

The next milestone is to persist a task run, expose the core through MCP, and add
the first deterministic governor checks. See [docs/roadmap.md](docs/roadmap.md).

