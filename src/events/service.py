"""Events service: orchestrates source → store → query.

Keep this module Quart-free so the HTTP/cron layer above is the only thing
that has to change when this becomes its own service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from . import enrich, source_kdb, source_ticketmaster
from .store import Event, EventStore

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 14
REFRESH_INTERVAL_SECONDS = 6 * 3600
AI_ENRICH_LIMIT = 2000  # cap per refresh as a cost guardrail

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
    """Pull Berlin events from all configured sources, replace each
    source's slice atomically, apply the heuristic to anything still
    uncategorised (Ticketmaster pre-tags from its own taxonomy), then
    kick off AI refinement in the background. Returns total events
    written across sources."""
    async with _refresh_lock:
        kdb_events, tm_events = await asyncio.gather(
            source_kdb.fetch_events(days=days),
            source_ticketmaster.fetch_events(days=days),
            return_exceptions=True,
        )
        store = get_store()
        total = 0

        if isinstance(kdb_events, list):
            kdb_events = enrich.apply_heuristic(kdb_events)
            total += store.replace_source(
                source_kdb.REGION, source_kdb.SOURCE_ID, kdb_events
            )
        else:
            logger.exception(
                "events.refresh_berlin: kulturdaten fetch failed",
                exc_info=kdb_events,
            )

        if isinstance(tm_events, list):
            # TM events arrive pre-categorised from the segment/genre map.
            # Run heuristic only on the rare uncategorised row so we always
            # have a category set.
            tm_events = enrich.apply_heuristic_if_missing(tm_events)
            total += store.replace_source(
                source_ticketmaster.REGION, source_ticketmaster.SOURCE_ID, tm_events
            )
        else:
            logger.exception(
                "events.refresh_berlin: ticketmaster fetch failed",
                exc_info=tm_events,
            )

        store.delete_before((date.today() - timedelta(days=1)).isoformat())
        logger.info("events.refresh_berlin: stored %d events for Berlin", total)

    if enrich.ai_enabled():
        asyncio.create_task(_ai_enrich_pending())
    return total


async def _ai_enrich_pending() -> None:
    """Classify events with no AI enrichment yet, write results back."""
    store = get_store()
    pending = store.query_unenriched(limit=AI_ENRICH_LIMIT)
    if not pending:
        return
    logger.info("events.enrich.ai: starting on %d events", len(pending))
    results = await enrich.classify_with_ai(pending)
    ts = enrich.now()
    for ev in pending:
        c = results.get(ev.id)
        if c is None:
            # Mark as enriched anyway so we don't re-attempt forever.
            store.update_classification(
                ev.id,
                category=ev.category,
                interest_score=ev.interest_score,
                is_civic=ev.is_civic,
                enriched_at=ts,
            )
            continue
        store.update_classification(
            ev.id,
            category=c.category,
            interest_score=c.interest_score,
            is_civic=c.is_civic,
            enriched_at=ts,
        )
    logger.info(
        "events.enrich.ai: applied %d classifications", len(results)
    )


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
