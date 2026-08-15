"""Append-only event log backed by the Sogi SQLite database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .event import Event

if TYPE_CHECKING:
    from sogi.storage.db import SogiDatabase


class EventLog:
    """Append-only log of :class:`Event` entries for a run.

    Events are never mutated or deleted; the database assigns a monotonic
    sequence number on append. The governor and future replay tooling read
    this stream rather than trusting derived RunRecord snapshots.
    """

    def __init__(self, db: SogiDatabase) -> None:
        self._db = db

    def append(self, event: Event) -> Event:
        """Persist an event and return it with its assigned sequence number."""
        return self._db.append_event(event)

    def for_run(self, run_id: str) -> list[Event]:
        """Return every event for a run in append order."""
        return self._db.events(run_id)
