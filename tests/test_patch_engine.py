"""Tests for the governed patch engine (propose → apply, stale protection)."""

import subprocess
from pathlib import Path

import pytest

from sogi.patch.ast_grep import AstGrepPatchProvider, _files_from_sg_diff
from sogi.patch.provider import (
    PatchError,
    PatchToolUnavailable,
    StaleTargetError,
    region_hash,
)
from sogi.repository.provider import RepositoryProvider, RepositorySnapshot, Symbol
from sogi.runs.service import PatchScopeError, RunService

AUTH_SOURCE = '''\
"""Auth module."""

class AuthService:
    def refresh_token(self):
        return validate_token()

    def login(self):
        return "ok"
'''


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "auth.py").write_text(AUTH_SOURCE)
    (root / "tests" / "test_auth.py").write_text("def test_refresh_token():\n    assert True\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


class StubProvider(RepositoryProvider):
    """Resolves refresh_token to the exact region on disk."""

    def prepare(self) -> dict:
        return {}

    def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
        return RepositorySnapshot(task=task, symbols=())

    def search_symbols(self, query: str, *, limit: int = 20) -> tuple[Symbol, ...]:
        return ()

    def get_symbol(self, symbol: str, *, file: str | None = None) -> Symbol | None:
        if symbol == "refresh_token":
            # Lines 5-6 of AUTH_SOURCE (1-based): the method body.
            return Symbol(
                name=symbol,
                file="src/auth.py",
                line=4,
                end_line=5,
                content="def refresh_token(self):\n        return validate_token()\n",
            )
        return None

    def callers(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        return ()

    def callees(self, symbol: str, *, file: str | None = None) -> tuple[Symbol, ...]:
        return ()

    def dependencies(self, file: str | None = None) -> dict:
        return {}

    def related_tests(self, files: tuple[str, ...]) -> tuple[str, ...]:
        return ("tests/test_auth.py",)


def region_of(source: str, start: int, end: int) -> str:
    lines = source.splitlines(keepends=True)
    return "".join(lines[start - 1 : end])


def test_region_hash_is_stable_and_content_sensitive() -> None:
    assert region_hash("abc") == region_hash("abc")
    assert region_hash("abc") != region_hash("abd")


def test_propose_computes_diff_without_modifying_disk(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id
    request = {
        "operation": "replace_symbol",
        "symbol": "refresh_token",
        "replacement": "def refresh_token(self):\n        return cached_token()\n",
    }

    record = service.propose_patch(run_id, request)

    assert record["status"] == "proposed"
    assert record["files"] == ["src/auth.py"]
    assert "+        return cached_token()" in record["diff"]
    assert "-    def refresh_token(self):" in record["diff"]
    # Nothing changed on disk yet.
    assert (repo / "src" / "auth.py").read_text() == AUTH_SOURCE


def test_stale_expected_hash_rejects_proposal(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id

    with pytest.raises(StaleTargetError):
        service.propose_patch(
            run_id,
            {
                "operation": "replace_symbol",
                "symbol": "refresh_token",
                "expected_hash": "stale00000000000",
                "replacement": "x",
            },
        )


def test_apply_writes_file_and_emits_events(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id
    proposal = service.propose_patch(
        run_id,
        {
            "operation": "replace_symbol",
            "symbol": "refresh_token",
            "replacement": "def refresh_token(self):\n        return cached_token()\n",
        },
    )

    applied = service.apply_patch(run_id, proposal["patch_id"])

    assert applied["status"] == "applied"
    assert "cached_token()" in (repo / "src" / "auth.py").read_text()
    events = service.events.for_run(run_id)
    types = [event.type for event in events]
    assert "patch_proposed" in types and "patch_applied" in types
    modifications = [event for event in events if event.type == "file_modified"]
    assert [event.payload["path"] for event in modifications] == ["src/auth.py"]


def test_apply_after_drift_is_rejected(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id
    proposal = service.propose_patch(
        run_id,
        {
            "operation": "replace_symbol",
            "symbol": "refresh_token",
            "replacement": "def refresh_token(self):\n        return cached_token()\n",
        },
    )
    # Concurrent edit between propose and apply.
    (repo / "src" / "auth.py").write_text(AUTH_SOURCE.replace("login", "signin"))

    with pytest.raises(PatchError, match="PATCH REJECTED"):
        service.apply_patch(run_id, proposal["patch_id"])
    assert (repo / "src" / "auth.py").read_text() == AUTH_SOURCE.replace("login", "signin")


def test_apply_is_idempotent_guarded(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id
    proposal = service.propose_patch(
        run_id,
        {
            "operation": "replace_symbol",
            "symbol": "refresh_token",
            "replacement": "def refresh_token(self):\n        return cached_token()\n",
        },
    )
    service.apply_patch(run_id, proposal["patch_id"])

    with pytest.raises(PatchError, match="already applied"):
        service.apply_patch(run_id, proposal["patch_id"])


def test_out_of_scope_apply_requires_acknowledgement(repo: Path) -> None:
    class NarrowProvider(StubProvider):
        def discover(self, task: str, *, limit: int = 30) -> RepositorySnapshot:
            return RepositorySnapshot(
                task=task,
                symbols=(),
                related_files=("tests/test_auth.py",),
                related_tests=("tests/test_auth.py",),
            )

    service = RunService(repo, provider=NarrowProvider(repo))
    run_id = service.start("Only touch tests").run_id
    proposal = service.propose_patch(
        run_id,
        {
            "operation": "replace_symbol",
            "symbol": "refresh_token",
            "replacement": "def refresh_token(self):\n        return 1\n",
        },
    )

    with pytest.raises(PatchScopeError) as excinfo:
        service.apply_patch(run_id, proposal["patch_id"])
    assert excinfo.value.files == ("src/auth.py",)
    # Rejection is auditable before any operator decision.
    warnings = service.get(run_id).telemetry.warnings
    assert any(w.kind == "scope_expansion" and w.subject == "src/auth.py" for w in warnings)

    # Explicit acknowledgement unblocks the same patch without re-proposing.
    service.acknowledge(run_id, "scope_expansion", "src/auth.py")
    applied = service.apply_patch(run_id, proposal["patch_id"])
    assert applied["status"] == "applied"


def test_event_stream_replays_patches_exactly(repo: Path) -> None:
    from sogi.events.replay import compare_with_snapshot, replay

    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("Fix refresh token handling", compile_context=False).run_id
    proposal = service.propose_patch(
        run_id,
        {
            "operation": "replace_symbol",
            "symbol": "refresh_token",
            "expected_hash": region_hash(
                region_of(AUTH_SOURCE, 4, 5)
            ),
            "replacement": "def refresh_token(self):\n        return cached_token()\n",
            "reason": "handle expired tokens",
        },
    )
    service.apply_patch(run_id, proposal["patch_id"])

    stored = service.get(run_id)
    rebuilt = replay(service.events.for_run(run_id))
    result = compare_with_snapshot(rebuilt, stored)
    assert result["mismatches"] == []
    assert stored.telemetry.patches[-1].reason == "handle expired tokens"


def test_unknown_operation_rejected(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("t", compile_context=False).run_id
    with pytest.raises(ValueError, match="Unsupported patch operation"):
        service.propose_patch(run_id, {"operation": "sed_everything"})


def test_empty_proposal_rejected(repo: Path) -> None:
    service = RunService(repo, provider=StubProvider(repo))
    run_id = service.start("t", compile_context=False).run_id
    with pytest.raises(PatchError, match="no changes"):
        service.propose_patch(
            run_id,
            {
                "operation": "replace_symbol",
                "symbol": "refresh_token",
                # Byte-identical to the current region: a no-op proposal.
                "replacement": region_of(AUTH_SOURCE, 4, 5),
            },
        )


# -- ast-grep provider --------------------------------------------------------


class FakeRunner:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.commands: list[list[str]] = []

    def __call__(self, command):
        self.commands.append(list(command))

        class Completed:
            pass

        completed = Completed()
        completed.stdout = self.stdout
        completed.stderr = ""
        completed.returncode = self.returncode
        return completed


PREVIEW_DIFF = """\
---
src/auth.py
------- -------
@@ ...
-        return validate_token()
+        return cached_token()
"""


def test_ast_grep_dry_run_parses_files(tmp_path: Path) -> None:
    runner = FakeRunner(stdout=PREVIEW_DIFF)
    provider = AstGrepPatchProvider(tmp_path, command="ast-grep", runner=runner)

    proposal = provider.dry_run({"pattern": "$A + $B", "rewrite": "$B + $A"})

    assert proposal.files == ("src/auth.py",)
    assert proposal.operation == "rewrite"
    assert runner.commands[0][1] == "run"
    assert "--update-all" not in runner.commands[0]  # dry run never applies


def test_ast_grep_apply_uses_update_all(tmp_path: Path) -> None:
    runner = FakeRunner(stdout=PREVIEW_DIFF)
    provider = AstGrepPatchProvider(tmp_path, command="ast-grep", runner=runner)

    provider.apply({"pattern": "foo($A)", "rewrite": "bar($A)", "paths": ["src/"]})

    command = runner.commands[-1]
    assert "-U" in command
    assert "src/" in command


def test_ast_grep_unavailable_raises_cleanly(tmp_path: Path) -> None:
    provider = AstGrepPatchProvider(tmp_path, command=None, runner=FakeRunner())
    assert not provider.available
    with pytest.raises(PatchToolUnavailable):
        provider.dry_run({"pattern": "a", "rewrite": "b"})


def test_sg_diff_file_extraction() -> None:
    diff = "---\nsrc/a.py\n--- ---\nsrc/b.py\nzzz\n---\nsrc/a.py\n"
    assert _files_from_sg_diff(diff) == ["src/a.py", "src/b.py"]
