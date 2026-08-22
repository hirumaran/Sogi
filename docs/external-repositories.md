# External reference repositories

Sogi owns its control-plane policy. External projects supply replaceable primitives,
test subjects, or benchmark infrastructure; their source is not vendored into the
Sogi package.

Local checkouts live under `external/`, which is intentionally ignored by
Git. They are shallow clones for implementation research and compatibility testing.
`external/revisions.json` (regenerate with `scripts/pin-revisions.py`, verify with
`--check`) records the exact commit each checkout points at; the doctor reports
drift between that file and the working clones.
Sogi must continue to work from published packages and command-line interfaces on a
clean installation.

## Current high-priority checkouts

| Project | Upstream | Local revision | Sogi decision |
| --- | --- | --- | --- |
| Tree-sitter Analyzer | <https://github.com/aimasteracc/tree-sitter-analyzer> | `335e78b750f347e852c96c4905b7443a71be3dd2` | Existing default repository-intelligence provider. Keep behind `RepositoryProvider`. |
| ast-grep | <https://github.com/ast-grep/ast-grep> | `0eb08389b6c4c5f3e19f90efbcb726fc413ca63d` | Powers `AstGrepPatchProvider` pattern rewrites through the documented CLI. Application remains governed by Sogi; never expose unrestricted rewrites. |
| MCP Python SDK | <https://github.com/modelcontextprotocol/python-sdk> | `57394b0548d1e2dc2dce8d67d84985769df3b8bb` | Compatibility reference. Sogi currently uses SDK v1/FastMCP; upstream v2 is stable and renames it to `MCPServer`. Plan and test a migration instead of copying SDK code. |
| CodeGraph | <https://github.com/colbymchenry/codegraph> | `44e1812d3b1c88cf8193732608345a5cf6941e30` | Experimental alternative `RepositoryProvider` using documented JSON CLI output. Do not replace the default until controlled parity and quality comparisons exist. |
| mini-SWE-agent | <https://github.com/SWE-agent/mini-swe-agent> | `25941c89cfbc91eb40b3f8756348c91d9977d57e` | First controlled external agent test subject. Adapt its explicit environment/model/trajectory boundary to the existing eval harness. |
| SWE-bench | <https://github.com/SWE-bench/SWE-bench> | `7a21e05772954cc81471ae19d56f436cecf43c54` | Real-task evaluation backend. Start with a small pinned slice and its Docker grader; do not make it part of the Sogi runtime dependency set. |

Revisions are snapshots of the local clones, not dependency pins. Before basing an
implementation on one, record the exact upstream revision in the experiment or pull
request that introduced the adapter.

## What the repository already has

- Tree-sitter-backed symbols, callers, callees, dependencies, affected tests, and
  bounded phase-aware MMR context selection.
- Persistent run records, append-only events, deterministic replay, and SQLite-backed
  projections.
- A working MCP v1 server with a thin SDK adapter and testable Sogi-owned facade.
- Claude Code MCP and hook integration with observation provenance and worktree
  reconciliation.
- Deterministic governor checks, patch risk assessment, verification watermarks,
  structured pytest/JUnit evidence, and restricted verification command launching.
- An isolated baseline-versus-Sogi evaluation harness with raw artifacts, independent
  grading, and host-reported usage fields.

The current `sogi.patch` package both assesses a finished diff *and* proposes/applies
governed structural edits (`PatchProvider`: hash-guarded region replacement plus an
ast-grep pattern-rewrite adapter, defaulting to dry-run with explicit apply).
Hierarchical localization (file → symbol → region, tiered) is wired through the CLI
(`sogi context --format localization`) and MCP (`localize`). Evidence providers beyond
pytest/JUnit and a production-grade external agent/benchmark runner are still missing.

## Integration order

1. ~~**ast-grep patch proposal.**~~ **Done.** `AstGrepPatchProvider` previews pattern
   rewrites via the documented CLI; application stays behind `RunService.apply_patch`
   with worktree-fingerprint staleness guards, scope checks, and auditable events.
   Install ast-grep to enable rewrite operations.
2. **MCP v2 compatibility.** Port the thin adapter from `FastMCP` to `MCPServer`, add a
   v2 in-memory protocol test, and update the supported dependency range only after the
   Claude end-to-end trial passes. Keep the `SogiMcp` facade SDK-independent.
3. **Semgrep evidence provider.** Add a normalized static-analysis provider so findings
   feed acceptance-criterion evidence alongside executed tests.
4. **CodeGraph provider spike.** Implement `CodeGraphProvider` through the documented
   JSON CLI (`query`, `callers`, `callees`, `impact`, and `affected`). Compare it against
   Tree-sitter Analyzer on identical internal tasks for context relevance, latency,
   selected tokens, and affected-test recall before deciding whether it ships.
5. **mini-SWE-agent runner.** Add a runner that pins its configuration and limits,
   captures its trajectory and reported usage, and executes identical baseline and Sogi
   arms in disposable workspaces.
6. **SWE-bench slice.** Translate pinned instances into Sogi eval tasks, preserve the
   base commit and instance ID, export predictions in the harness format, and grade in
   Docker. Start with a resource-bounded smoke slice before broader runs.
7. **Comby fallback.** Only if ast-grep language coverage proves insufficient for real
   patch requests.

## Deferred repositories

AgentTrace, Semgrep, Comby, and OpenHands remain medium/later references. Their current
roles overlap working Sogi components or depend on infrastructure that is not yet the
critical path:

- Semgrep becomes useful when the verifier gets a normalized static-analysis evidence
  provider.
- Comby is a possible fallback only if ast-grep language coverage proves insufficient.
- AgentTrace may inform reports and manual-evidence UX, but Sogi already owns provenance,
  event sourcing, replay, and telemetry semantics.
- OpenHands belongs with later sandboxed large-scale experiments after mini-SWE-agent and
  the internal evaluation suite are stable.

