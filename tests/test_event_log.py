from pathlib import Path

import pytest

from sogi.events.event import EVENT_TYPES, Event
from sogi.storage.db import SogiDatabase


@pytest.fixture()
def db(tmp_path: Path) -> SogiDatabase:
    database = SogiDatabase(tmp_path / ".sogi")
    yield database
    database.close()


def test_event_requires_known_type() -> None:
    with pytest.raises(ValueError):
        Event(type="mystery_event", run_id="abc")


def test_append_assigns_monotonic_sequence(db: SogiDatabase) -> None:
    first = db.append_event(Event(type="task_created", run_id="abc"))
    second = db.append_event(Event(type="file_read", run_id="abc", payload={"path": "a.py"}))
    third = db.append_event(Event(type="file_read", run_id="def", payload={"path": "b.py"}))

    assert first.sequence < second.sequence < third.sequence


def test_events_are_append_only_and_ordered(db: SogiDatabase) -> None:
    db.append_event(Event(type="task_created", run_id="abc"))
    db.append_event(Event(type="file_read", run_id="abc", payload={"path": "a.py"}))
    db.append_event(Event(type="file_read", run_id="abc", payload={"path": "b.py"}))

    events = db.events("abc")

    assert [event.type for event in events] == ["task_created", "file_read", "file_read"]
    assert [event.payload["path"] for event in events[1:]] == ["a.py", "b.py"]
    assert all(event.run_id == "abc" for event in events)


def test_events_are_scoped_per_run(db: SogiDatabase) -> None:
    db.append_event(Event(type="task_created", run_id="abc"))
    db.append_event(Event(type="task_created", run_id="def"))

    assert len(db.events("abc")) == 1
    assert len(db.events("def")) == 1
    assert len(db.all_events()) == 2


def test_event_round_trip(db: SogiDatabase) -> None:
    original = Event(
        type="decision_recorded",
        run_id="abc",
        payload={"decision": "Use middleware"},
    )
    stored = db.append_event(original)
    loaded = db.events("abc")[0]

    assert loaded.type == original.type
    assert loaded.run_id == original.run_id
    assert loaded.payload == original.payload
    assert loaded.sequence == stored.sequence


def test_event_types_cover_the_lifecycle() -> None:
    expected = {
        "task_created",
        "context_compiled",
        "file_read",
        "file_modified",
        "command_started",
        "command_finished",
        "decision_recorded",
        "phase_changed",
        "warning_raised",
        "verification_started",
        "verification_result",
        "usage_recorded",
        "run_completed",
    }
    assert expected == EVENT_TYPES
