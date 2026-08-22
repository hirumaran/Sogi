"""Tests for governor severity and the acknowledgment policy."""

from pathlib import Path

import pytest
from fakes import FakeProvider

from sogi.runs.service import CompletionGateError, RunService


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture()
def service(repo: Path) -> RunService:
    return RunService(repo, provider=FakeProvider(repo))


def test_scope_expansion_is_high_severity_and_blocks(repo: Path) -> None:
    service = RunService(repo, provider=FakeProvider(repo))
    run_id = service.start("Fix expired refresh token redirect").run_id

    service.record_file_modified(run_id, "billing/charge.py")

    record = service.get(run_id)
    expansion = [w for w in record.telemetry.warnings if w.kind == "scope_expansion"]
    assert len(expansion) == 1
    assert expansion[0].severity == "HIGH"

    # Even with fresh passing verification, the HIGH finding blocks.
    from sogi.verification.discovery import DiscoveredCheck

    service.verify(
        run_id,
        checks=(DiscoveredCheck(name="t", command="exit 0", kind="test"),),
    )
    with pytest.raises(CompletionGateError) as excinfo:
        service.complete(run_id, allow_unverified=True)
    assert "Unresolved high-severity finding" in str(excinfo.value)


def test_acknowledgement_unlocks_completion(repo: Path) -> None:
    from sogi.verification.discovery import DiscoveredCheck

    service = RunService(repo, provider=FakeProvider(repo))
    run_id = service.start("Fix expired refresh token redirect").run_id

    service.record_file_modified(run_id, "billing/charge.py")
    service.acknowledge(run_id, "scope_expansion", "billing/charge.py")
    service.verify(
        run_id,
        checks=(DiscoveredCheck(name="t", command="exit 0", kind="test"),),
    )

    record = service.complete(run_id)
    assert record.telemetry.outcome == "completed"  # no criteria -> plain PASS
    ack_events = [
        e.payload
        for e in service.events.for_run(run_id)
        if e.type == "decision_recorded" and e.payload.get("kind") == "acknowledge"
    ]
    assert any(
        p.get("warning_kind") == "scope_expansion" and p.get("subject") == "billing/charge.py"
        for p in ack_events
    )


def test_warning_level_findings_do_not_block(service: RunService) -> None:
    from sogi.verification.discovery import DiscoveredCheck

    run_id = service.start("Fix auth", compile_context=False).run_id
    for _ in range(3):
        service.record_file_read(run_id, "src/auth.py")

    warnings = service.get(run_id).telemetry.warnings
    assert any(w.severity == "WARNING" and w.kind == "repeated_read" for w in warnings)

    service.verify(run_id, checks=(DiscoveredCheck(name="t", command="exit 0", kind="test"),))
    record = service.complete(run_id, allow_unverified=True)
    assert record.state.phase.value == "done"
