# Sogi roadmap

## Completed foundation

1. Validate Tree-sitter Analyzer instead of assuming its capabilities.
2. Establish Sogi as an independent Python package.
3. Define the repository-provider boundary.
4. Compile ranked repository context under a fixed token budget.
5. Expose the vertical slice through `sogi context`.

## Next

1. Create a run record that binds `TaskSpec`, `EngineeringState`, compiled context,
   and telemetry.
2. Expose `understand_task`, `get_context`, `get_state`, and `record_decision`
   through an MCP server.
3. Add deterministic scope-expansion, repeated-read, and repeated-failure checks.
4. Add an independent verification command that runs repository-declared checks and
   maps evidence back to acceptance criteria.
5. Integrate one coding agent only after the control-plane API is stable.

Benchmarking, multi-agent support, UI work, and fault injection intentionally remain
later phases. No performance claim should be published without controlled results.

