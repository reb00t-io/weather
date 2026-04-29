"""SQLite-backed event store.

Schema is intentionally flat — every column comes straight off the source
event payload, so queries stay simple and migration to another store later
is just a copy. Concurrency mirrors the CityImageCache pattern: WAL for
non-blocking reads, a threading lock to serialise writes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Event:
    id: str
    region: str
    start_date: str           # ISO YYYY-MM-DD
    end_date: str | None
    start_time: str | None    # HH:MM:SS or None for all-day
    end_time: str | None
    title: str
    venue: str | None
    is_free: bool
    source: str
    refreshed_at: float

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "region": self.region,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "title": self.title,
            "venue": self.venue,
            "is_free": self.is_free,
            "source": self.source,
        }


class EventStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = os.environ.get(
                "EVENTS_DB_PATH",
                str(Path.home() / ".cache" / "weather" / "events.db"),
            )
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id            TEXT PRIMARY KEY,
                        region        TEXT NOT NULL,
                        start_date    TEXT NOT NULL,
                        end_date      TEXT,
                        start_time    TEXT,
                        end_time      TEXT,
                        title         TEXT NOT NULL,
                        venue         TEXT,
                        is_free       INTEGER NOT NULL DEFAULT 0,
                        source        TEXT NOT NULL,
                        refreshed_at  REAL NOT NULL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS events_region_date "
                    "ON events(region, start_date)"
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_many(self, events: Iterable[Event]) -> int:
        rows = [
            (
                e.id, e.region, e.start_date, e.end_date,
                e.start_time, e.end_time, e.title, e.venue,
                1 if e.is_free else 0, e.source, e.refreshed_at,
            )
            for e in events
        ]
        if not rows:
            return 0
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO events "
                    "(id, region, start_date, end_date, start_time, end_time, "
                    " title, venue, is_free, source, refreshed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        return len(rows)

    def query(
        self,
        region: str,
        start_date: str,
        end_date: str,
    ) -> list[Event]:
        """Return events for `region` with start_date in [start_date, end_date],
        ordered by start_date, start_time (NULLs last so all-day rows fall
        below same-day timed rows)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events "
                "WHERE region = ? AND start_date BETWEEN ? AND ? "
                "ORDER BY start_date ASC, "
                "         CASE WHEN start_time IS NULL OR start_time = '' "
                "              THEN 1 ELSE 0 END, "
                "         start_time ASC, title ASC",
                (region, start_date, end_date),
            ).fetchall()
        finally:
            conn.close()
        return [
            Event(
                id=r["id"],
                region=r["region"],
                start_date=r["start_date"],
                end_date=r["end_date"],
                start_time=r["start_time"],
                end_time=r["end_time"],
                title=r["title"],
                venue=r["venue"],
                is_free=bool(r["is_free"]),
                source=r["source"],
                refreshed_at=r["refreshed_at"],
            )
            for r in rows
        ]

    def delete_before(self, cutoff_date: str) -> int:
        """Delete events that ended before cutoff_date (ISO YYYY-MM-DD).
        Returns the number of rows removed."""
        with self._write_lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM events "
                    "WHERE COALESCE(end_date, start_date) < ?",
                    (cutoff_date,),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def replace_region(self, region: str, events: Iterable[Event]) -> int:
        """Replace all events for `region` atomically. Returns inserted count."""
        events = list(events)
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM events WHERE region = ?", (region,))
                if events:
                    conn.executemany(
                        "INSERT INTO events "
                        "(id, region, start_date, end_date, start_time, end_time, "
                        " title, venue, is_free, source, refreshed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                e.id, e.region, e.start_date, e.end_date,
                                e.start_time, e.end_time, e.title, e.venue,
                                1 if e.is_free else 0, e.source, e.refreshed_at,
                            )
                            for e in events
                        ],
                    )
                conn.commit()
            finally:
                conn.close()
        return len(events)


def now_ts() -> float:
    return time.time()
