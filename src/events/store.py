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
    # Classification (filled by enrich.py — null means not yet classified)
    category: str | None = None         # music|stage|art|family|market|sports|talk|festival|civic|other
    interest_score: int | None = None   # 0..3
    is_civic: bool | None = None        # True for bureaucratic/admin items
    enriched_at: float | None = None    # timestamp of last classifier run

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
            "category": self.category,
            "interest_score": self.interest_score,
            "is_civic": self.is_civic,
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
                        id              TEXT PRIMARY KEY,
                        region          TEXT NOT NULL,
                        start_date      TEXT NOT NULL,
                        end_date        TEXT,
                        start_time      TEXT,
                        end_time        TEXT,
                        title           TEXT NOT NULL,
                        venue           TEXT,
                        is_free         INTEGER NOT NULL DEFAULT 0,
                        source          TEXT NOT NULL,
                        refreshed_at    REAL NOT NULL,
                        category        TEXT,
                        interest_score  INTEGER,
                        is_civic        INTEGER,
                        enriched_at     REAL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS events_region_date "
                    "ON events(region, start_date)"
                )
                # Idempotent migrations for databases created before classification.
                existing = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
                for col, ddl in (
                    ("category",       "ALTER TABLE events ADD COLUMN category TEXT"),
                    ("interest_score", "ALTER TABLE events ADD COLUMN interest_score INTEGER"),
                    ("is_civic",       "ALTER TABLE events ADD COLUMN is_civic INTEGER"),
                    ("enriched_at",    "ALTER TABLE events ADD COLUMN enriched_at REAL"),
                ):
                    if col not in existing:
                        conn.execute(ddl)
                conn.commit()
            finally:
                conn.close()

    def upsert_many(self, events: Iterable[Event]) -> int:
        """Insert or replace events. Preserves AI-completed classifications
        (rows where enriched_at is set) so the AI layer's results survive
        re-imports; otherwise the new event's classification (typically the
        heuristic just applied) wins."""
        events = list(events)
        if not events:
            return 0
        with self._write_lock:
            conn = self._connect()
            try:
                ids = [e.id for e in events]
                placeholders = ",".join("?" * len(ids))
                existing = {
                    r["id"]: r
                    for r in conn.execute(
                        f"SELECT id, category, interest_score, is_civic, enriched_at "
                        f"FROM events WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                }
                rows = [
                    self._merge_for_upsert(e, existing.get(e.id))
                    for e in events
                ]
                conn.executemany(
                    "INSERT OR REPLACE INTO events "
                    "(id, region, start_date, end_date, start_time, end_time, "
                    " title, venue, is_free, source, refreshed_at, "
                    " category, interest_score, is_civic, enriched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        return len(rows)

    @staticmethod
    def _merge_for_upsert(e: "Event", prev) -> tuple:
        """Pick the classification fields to write for this row.

        Rule: if the prior row was AI-enriched (enriched_at is set), keep
        the prior values — the AI's verdict outranks the heuristic. Otherwise
        the new event's values win (typically a fresh heuristic pass)."""
        if prev is not None and prev["enriched_at"] is not None:
            category = prev["category"]
            score = prev["interest_score"]
            civic = prev["is_civic"]
            enriched = prev["enriched_at"]
        else:
            category = e.category
            score = e.interest_score
            civic = (1 if e.is_civic else 0) if e.is_civic is not None else None
            enriched = e.enriched_at
        return (
            e.id, e.region, e.start_date, e.end_date,
            e.start_time, e.end_time, e.title, e.venue,
            1 if e.is_free else 0, e.source, e.refreshed_at,
            category, score, civic, enriched,
        )

    def update_classification(
        self,
        event_id: str,
        *,
        category: str | None,
        interest_score: int | None,
        is_civic: bool | None,
        enriched_at: float,
    ) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE events SET category = ?, interest_score = ?, "
                    "is_civic = ?, enriched_at = ? WHERE id = ?",
                    (
                        category,
                        interest_score,
                        (1 if is_civic else 0) if is_civic is not None else None,
                        enriched_at,
                        event_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def query_unenriched(self, limit: int = 500) -> list[Event]:
        """Return events whose AI enrichment has not yet run."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE enriched_at IS NULL "
                "ORDER BY start_date ASC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_event(r) for r in rows]

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
        return [_row_to_event(r) for r in rows]

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
        """Replace all events for `region` atomically, preserving enrichment
        for any IDs that already existed. Returns inserted count."""
        events = list(events)
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                # Capture enrichment of existing region rows so we can carry it
                # forward to re-imports of the same event ID.
                prior = {
                    r["id"]: r
                    for r in conn.execute(
                        "SELECT id, category, interest_score, is_civic, enriched_at "
                        "FROM events WHERE region = ?",
                        (region,),
                    ).fetchall()
                }
                conn.execute("DELETE FROM events WHERE region = ?", (region,))
                if events:
                    rows = [
                        self._merge_for_upsert(e, prior.get(e.id))
                        for e in events
                    ]
                    conn.executemany(
                        "INSERT INTO events "
                        "(id, region, start_date, end_date, start_time, end_time, "
                        " title, venue, is_free, source, refreshed_at, "
                        " category, interest_score, is_civic, enriched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                conn.commit()
            finally:
                conn.close()
        return len(events)


def _row_to_event(r) -> Event:
    """Build an Event from a sqlite3.Row, tolerating older rows that lack
    the classification columns."""
    keys = r.keys()
    return Event(
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
        category=r["category"] if "category" in keys else None,
        interest_score=r["interest_score"] if "interest_score" in keys else None,
        is_civic=(bool(r["is_civic"]) if r["is_civic"] is not None else None) if "is_civic" in keys else None,
        enriched_at=r["enriched_at"] if "enriched_at" in keys else None,
    )


def now_ts() -> float:
    return time.time()
