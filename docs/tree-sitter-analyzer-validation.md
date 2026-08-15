# Tree-sitter Analyzer validation

Validated locally against `examples/demo_repo` with Tree-sitter Analyzer 1.29.0.

## Confirmed structured capabilities

| Sogi need | Analyzer CLI | Observed result |
|---|---|---|
| Build repository index | `--full-index --full-index-mode full` | 3 files, 8 symbols, FTS5 ready |
| Task-oriented discovery | `--codegraph-context TASK` | Entry points, related symbols/files, source blocks |
| Symbol search | `--symbol-search QUERY` | BM25-ranked definitions with source bodies |
| Definition and references | `--codegraph-navigate SYMBOL` | Definition, references, and hierarchy |
| Callers | `--callers SYMBOL` | Two resolved callers in the fixture |
| Callees | `--callees SYMBOL` | One resolved callee in the fixture |
| Dependencies | `--dependencies summary` | File graph with nodes, edges, and hubs |
| Related tests | `--affected FILE...` | `tests/test_auth.py` for `auth.py` |

All commands supported `--format json` and `--project-root`.

## Environment note

`rg` was available. `fd` was not installed, so analyzer content-search features that
require `fd` are unavailable in this environment. The indexed symbol and call-graph
features used by the Sogi MVP do not require it.

## Adapter decision

Sogi calls the analyzer CLI as a subprocess and normalizes its JSON. Core modules do
not import analyzer internals. This keeps the provider replaceable and gives Sogi a
stable failure boundary when analyzer versions or implementations change.

