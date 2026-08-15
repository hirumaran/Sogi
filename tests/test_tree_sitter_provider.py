import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from sogi.repository.tree_sitter_provider import TreeSitterProvider


def _runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    if "--codegraph-context" in command:
        payload = {
            "code_blocks": [
                {
                    "file": "auth.py",
                    "name": "refresh_token",
                    "start_line": 3,
                    "end_line": 5,
                    "content": "def refresh_token():\n    pass\n",
                }
            ],
            "entry_points": [{"name": "refresh_token"}],
            "related_files": ["auth.py", "tests/test_auth.py"],
            "stats": {"nodes_total": 2},
        }
    elif "--affected" in command:
        payload = {"test_files": ["tests/test_auth.py"]}
    else:
        payload = {"success": True, "total_symbols": 1}
    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")


def test_provider_normalizes_public_cli_json(tmp_path: Path) -> None:
    provider = TreeSitterProvider(tmp_path, command=("tsa",), runner=_runner)

    snapshot = provider.discover("fix refresh")

    assert snapshot.symbols[0].name == "refresh_token"
    assert snapshot.symbols[0].relevance == 1.0
    assert snapshot.related_tests == ("tests/test_auth.py",)
    assert provider.related_tests(("auth.py",)) == ("tests/test_auth.py",)
