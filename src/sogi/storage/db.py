"""SQLite persistence for Sogi runs and their append-only event log.

Layout under the repository root::

    .sogi/
    ├── sogi.db      # runs table (JSON payload) + events table (append-only)
    └── runs/        # human-readable JSON snapshot per run

The database is the source of truth; the per-run JSON files are derived
mirrors written on every save so a run can be inspected without a SQL client.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from sogi.core.run_record import RunRecord
from sogi.events.event import Event

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
"""


class SogiDatabase:
    """Owns the SQLite connection and the run snapshot directory."""

    def __init__(self, sogi_dir: Path) -> None:
        self.sogi_dir = sogi_dir.expanduser().resolve()
        self.db_path = self.sogi_dir / "sogi.db"
        self.snapshots_dir = self.sogi_dir / "runs"
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.sogi_dir.mkdir(parents=True, exist_ok=True)
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- runs ----------------------------------------------------------------

    def save_run(self, record: RunRecord) -> None:
        payload = json.dumps(record.to_dict(), sort_keys=True)
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO runs (run_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (record.run_id, payload, record.created_at, record.updated_at),
            )
            conn.commit()
            self._write_snapshot(record)

    def load_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunRecord.from_dict(json.loads(row["payload"]))

    def list_runs(self) -> list[RunRecord]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT payload FROM runs ORDER BY created_at").fetchall()
        return [RunRecord.from_dict(json.loads(row["payload"])) for row in rows]

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
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "INSERT INTO events (run_id, type, timestamp, payload) VALUES (?, ?, ?, ?)",
                (
                    event.run_id,
                    event.type,
                    event.timestamp,
                    json.dumps(event.payload, sort_keys=True),
                ),
            )
            conn.commit()
            sequence = int(cursor.lastrowid)
        return Event(
            type=event.type,
            run_id=event.run_id,
            timestamp=event.timestamp,
            sequence=sequence,
            payload=event.payload,
        )

    def events(self, run_id: str) -> list[Event]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT sequence, run_id, type, timestamp, payload FROM events "
                "WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            Event(
                type=row["type"],
                run_id=row["run_id"],
                timestamp=row["timestamp"],
                sequence=int(row["sequence"]),
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def all_events(self) -> list[Event]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT sequence, run_id, type, timestamp, payload FROM events ORDER BY sequence"
            ).fetchall()
        return [
            Event(
                type=row["type"],
                run_id=row["run_id"],
                timestamp=row["timestamp"],
                sequence=int(row["sequence"]),
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def __enter__(self) -> SogiDatabase:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
