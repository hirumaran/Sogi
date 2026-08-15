"""Human-readable rendering of runs and their event log."""

from __future__ import annotations

from sogi.core.run_record import RunRecord
from sogi.events.event import Event


def render_run_start(record: RunRecord) -> str:
    """Compact summary shown immediately after ``sogi run start``."""
    criteria = record.task.acceptance_criteria
    selected = record.telemetry.context_tokens[-1] if record.telemetry.context_tokens else None
    lines = [
        f"Run: {record.run_id}",
        "",
        "Objective:",
        record.task.objective,
        "",
        "Phase:",
        record.state.phase.value.upper(),
        "",
        "Acceptance criteria:",
        str(len(criteria)),
        "",
        "Context budget:",
        str(record.telemetry.context_budget),
        "",
        "Context selected:",
        f"{selected} tokens" if selected is not None else "not compiled",
    ]
    return "\n".join(lines)


def render_run_state(record: RunRecord) -> str:
    """Full engineering state for ``sogi run show``."""
    lines = [
        f"SOGI RUN {record.run_id}",
        "=" * (10 + len(record.run_id)),
        "",
        "OBJECTIVE",
        record.task.objective,
        "",
        "PHASE",
        record.state.phase.value.upper(),
        "",
        "ACCEPTANCE CRITERIA",
        *_numbered(record.task.acceptance_criteria, "Not explicitly provided"),
        "",
        "CONSTRAINTS",
        *_numbered(record.task.constraints, "None explicitly provided"),
        "",
        "CONTEXT",
        *_context_lines(record),
        "",
        "FILES EXAMINED",
        *_numbered(record.state.files_examined, "None yet"),
        "",
        "FILES MODIFIED",
        *_numbered(record.state.files_modified, "None yet"),
        "",
        "DECISIONS",
        *_numbered(record.state.decisions, "None yet"),
        "",
        "FAILED APPROACHES",
        *_numbered(record.state.failed_approaches, "None yet"),
        "",
        "COMMANDS",
        *_command_lines(record),
        "",
        "SOGI WARNINGS / INTERVENTIONS",
        *_warning_lines(record),
        "",
        "VERIFICATION",
        *_verification_lines(record),
        "",
        "TIMELINE",
        f"Created: {record.created_at}",
        f"Updated: {record.updated_at}",
        f"Completed: {record.telemetry.completed_at or 'not completed'}",
    ]
    return "\n".join(lines)


def render_events(events: list[Event]) -> str:
    """Render a run's event log in append order."""
    if not events:
        return "No events recorded."
    lines = ["SOGI EVENT LOG", "==============", ""]
    for event in events:
        detail = _event_detail(event)
        lines.append(f"{event.sequence:>4}  {event.timestamp}  {event.type}{detail}")
    return "\n".join(lines)


def _numbered(items: tuple[str, ...] | list[str], empty: str) -> list[str]:
    if not items:
        return [empty]
    return [f"{index}. {item}" for index, item in enumerate(items, 1)]


def _context_lines(record: RunRecord) -> list[str]:
    context = record.context
    if context is None:
        return ["Not compiled yet. Run `sogi context --run <id>`."]
    lines = [
        f"Selected: {context.selected_tokens} / {context.token_budget} tokens",
        f"Repository estimate: {context.repository_estimated_tokens} tokens",
        f"Compilations: {record.telemetry.context_compilations}",
        "Related files:",
        *_numbered(context.related_files, "None"),
        "Related tests:",
        *_numbered(context.related_tests, "None"),
        "Next investigation:",
        context.suggested_next_investigation,
    ]
    return lines


def _command_lines(record: RunRecord) -> list[str]:
    if not record.telemetry.commands:
        return ["None yet"]
    lines: list[str] = []
    for item in record.telemetry.commands:
        status = (
            "running"
            if item.finished_at is None
            else _command_status(item.success, item.exit_code)
        )
        lines.append(f"- {item.command} [{status}]")
        if item.result:
            lines.append(f"    {item.result}")
    return lines


def _command_status(success: bool | None, exit_code: int | None) -> str:
    if success is True:
        return "ok"
    if success is False:
        return "failed"
    if exit_code is not None:
        return f"exit {exit_code}"
    return "finished"


def _warning_lines(record: RunRecord) -> list[str]:
    if not record.telemetry.warnings:
        return ["None"]
    return [f"- [{item.kind}] {item.message}" for item in record.telemetry.warnings]


def _verification_lines(record: RunRecord) -> list[str]:
    if not record.telemetry.verification:
        return ["No verification recorded yet"]
    lines: list[str] = []
    for item in record.telemetry.verification:
        lines.append(f"- {item.criterion}: {item.status}")
        for evidence in item.evidence:
            lines.append(f"    {evidence}")
    return lines


def _event_detail(event: Event) -> str:
    payload = event.payload
    if event.type == "file_read" or event.type == "file_modified":
        return f"  {payload.get('path', '')}"
    if event.type == "command_started" or event.type == "command_finished":
        return f"  {payload.get('command', '')}"
    if event.type == "decision_recorded":
        return f"  {payload.get('decision', '')}"
    if event.type == "phase_changed":
        return f"  {payload.get('from', '')} -> {payload.get('to', '')}"
    if event.type == "warning_raised":
        return f"  [{payload.get('kind', '')}] {payload.get('message', '')}"
    if event.type == "context_compiled":
        return f"  {payload.get('selected_tokens', '?')} tokens"
    if event.type == "verification_result":
        return f"  {payload.get('criterion', '')}: {payload.get('status', '')}"
    return ""
