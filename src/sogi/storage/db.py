"""SQLite persistence for Sogi runs and their append-only event log.

Layout under the repository root::

    .sogi/
    ├── sogi.db      # runs table (JSON payload) + events table (append-only)
    └── runs/        # human-readable JSON snapshot per run

The database is the source of truth; the per-run JSON files are derived
mirrors written on every save so a run can be inspected without a SQL client.

Connection lifecycle: every operation opens its own short-lived connection
and closes it deterministically, so no handle can leak across process death,
garbage collection, or forgotten ``close()`` calls. Multi-step operations use
:meth:`SogiDatabase.transaction`, which commits once on success and rolls
back on any failure — event stream and projection update together or not at
all.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sogi.core.run_record import RunRecord
from sogi.events.event import Event

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, sequence);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SogiDatabase:
    """Owns the SQLite database file and the run snapshot directory."""

    def __init__(self, sogi_dir: Path) -> None:
        self.sogi_dir = sogi_dir.expanduser().resolve()
        self.db_path = self.sogi_dir / "sogi.db"
        self.snapshots_dir = self.sogi_dir / "runs"

    # -- connections ---------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection; commit on success, close always."""
        self.sogi_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_SCHEMA)
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit multi-statement transaction: all-or-nothing."""
        with self._connect() as conn:
            yield conn

    def close(self) -> None:  # kept for API compatibility; nothing persists
        pass

    # -- schema --------------------------------------------------------------

    def schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return int(row["value"]) if row else _SCHEMA_VERSION

    def _stamp_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )

    # -- runs ----------------------------------------------------------------

    def save_run(self, record: RunRecord) -> None:
        payload = json.dumps(record.to_dict(), sort_keys=True)
        with self._connect() as conn:
            self._stamp_schema(conn)
            self._upsert_run(conn, record.run_id, payload, record.created_at, record.updated_at)
        self._write_snapshot(record)

    def save_run_with_events(self, record: RunRecord, events: list[Event]) -> list[Event]:
        """Atomically persist the projection and append events.

        Either the run snapshot and all events commit, or neither does. A
        crash between the two is impossible by construction.
        """
        payload = json.dumps(record.to_dict(), sort_keys=True)
        persisted: list[Event] = []
        with self.transaction() as conn:
            self._stamp_schema(conn)
            self._upsert_run(conn, record.run_id, payload, record.created_at, record.updated_at)
            for event in events:
                persisted.append(self._insert_event(conn, event))
        self._write_snapshot(record)
        return persisted

    @staticmethod
    def _upsert_run(
        conn: sqlite3.Connection, run_id: str, payload: str, created_at: str, updated_at: str
    ) -> None:
        conn.execute(
            "INSERT INTO runs (run_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload, "
            "updated_at = excluded.updated_at",
            (run_id, payload, created_at, updated_at),
        )

    def load_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord.from_dict(json.loads(row["payload"]))

    def list_runs(self) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM runs ORDER BY created_at").fetchall()
        return [RunRecord.from_dict(json.loads(row["payload"])) for row in rows]

    def delete_run_snapshot_state(self, run_id: str) -> None:
        """Remove the projection row (events are never deleted)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        target = self.snapshots_dir / f"{run_id}.json"
        target.unlink(missing_ok=True)

    def _write_snapshot(self, record: RunRecord) -> None:
        target = self.snapshots_dir / f"{record.run_id}.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{record.run_id}-", dir=self.snapshots_dir
        )
        try:
            with open(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, indent=2, sort_keys=True)
                handle.write("\n")
            Path(temporary).replace(target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    # -- events --------------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        with self._connect() as conn:
            return self._insert_event(conn, event)

    @staticmethod
    def _insert_event(conn: sqlite3.Connection, event: Event) -> Event:
        cursor = conn.execute(
            "INSERT INTO events (run_id, type, timestamp, payload) VALUES (?, ?, ?, ?)",
            (
                event.run_id,
                event.type,
                event.timestamp,
                json.dumps(event.payload, sort_keys=True),
            ),
        )
        sequence = int(cursor.lastrowid)
        return Event(
            type=event.type,
            run_id=event.run_id,
            timestamp=event.timestamp,
            sequence=sequence,
            payload=event.payload,
        )

    def events(self, run_id: str) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sequence, run_id, type, timestamp, payload FROM events "
                "WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def last_sequence_of_type(self, run_id: str, event_type: str) -> int | None:
        """Latest sequence for an event type; None when it never occurred."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(sequence) AS seq FROM events WHERE run_id = ? AND type = ?",
                (run_id, event_type),
            ).fetchone()
        return int(row["seq"]) if row and row["seq"] is not None else None

    def max_sequence(self, run_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(sequence) AS seq FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row["seq"]) if row and row["seq"] is not None else 0

    def all_events(self) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sequence, run_id, type, timestamp, payload FROM events ORDER BY sequence"
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            type=row["type"],
            run_id=row["run_id"],
            timestamp=row["timestamp"],
            sequence=int(row["sequence"]),
            payload=json.loads(row["payload"]),
        )

    def __enter__(self) -> SogiDatabase:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
