"""Events service: orchestrates source → store → query.

Keep this module Quart-free so the HTTP/cron layer above is the only thing
that has to change when this becomes its own service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from . import source_kdb
from .store import Event, EventStore

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 14
REFRESH_INTERVAL_SECONDS = 6 * 3600

_store: EventStore | None = None
_refresh_lock = asyncio.Lock()


def get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore()
    return _store


def set_store(store: EventStore) -> None:
    """Inject a store (used by tests)."""
    global _store
    _store = store


async def refresh_berlin(days: int = DEFAULT_DAYS) -> int:
    """Pull events for Berlin from kulturdaten and replace the region's rows.
    Returns the count of events written."""
    async with _refresh_lock:
        events = await source_kdb.fetch_events(days=days)
        store = get_store()
        n = store.replace_region(source_kdb.REGION, events)
        store.delete_before((date.today() - timedelta(days=1)).isoformat())
        logger.info("events.refresh_berlin: stored %d events for Berlin", n)
        return n


def query_events(
    region: str,
    *,
    days: int = DEFAULT_DAYS,
    today: date | None = None,
) -> list[Event]:
    """Return today..today+days events for `region` (case-sensitive match)."""
    if today is None:
        today = date.today()
    end = today + timedelta(days=days)
    return get_store().query(region, today.isoformat(), end.isoformat())


async def background_refresher(
    interval_seconds: int = REFRESH_INTERVAL_SECONDS,
) -> None:
    """Run an immediate refresh, then loop forever refreshing every
    `interval_seconds`. Logs errors and keeps going."""
    while True:
        try:
            await refresh_berlin()
        except Exception:
            logger.exception("events.background_refresher: refresh failed")
        await asyncio.sleep(interval_seconds)
