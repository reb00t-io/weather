"""Events service: orchestrates source → store → query.

Keep this module Quart-free so the HTTP/cron layer above is the only thing
that has to change when this becomes its own service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from . import enrich, source_kdb, source_kinoheld, source_ticketmaster, source_yorck
from .store import Event, EventStore

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 14
REFRESH_INTERVAL_SECONDS = 6 * 3600
AI_ENRICH_LIMIT = 2000  # cap per refresh as a cost guardrail

# Cinema-source coverage. Each region is fetched independently from
# Kinoheld around its centre; the radius is the source-side cap that
# stuffs the cache, the frontend then narrows further (15 → 30 km from
# the user's actual location).
CINEMA_REGIONS: dict[str, dict] = {
    # Berlin spans ~25 km from centre; +30 km buffer means a user at
    # the city edge still sees their 30-km cinema circle covered.
    "Berlin":    {"lat": 52.520008, "lon": 13.404954, "radius_km": 40},
    "Salzwedel": {"lat": 52.854,    "lon": 11.155,    "radius_km": 35},
}

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


async def refresh(days: int = DEFAULT_DAYS) -> int:
    """Pull events for every configured region from every source we
    cover, replace each (region, source) slice atomically, apply the
    heuristic to anything still uncategorised, then kick off AI
    refinement in the background. Returns total events written."""
    async with _refresh_lock:
        # Berlin gets the whole stack; other regions are cinema-only
        # because kdb/ticketmaster are tuned for Berlin geography.
        kdb_task = source_kdb.fetch_events(days=days)
        tm_task = source_ticketmaster.fetch_events(days=days)
        # Yorck covers a handful of Berlin cinemas (e.g. Babylon Kreuzberg)
        # that Kinoheld doesn't carry — keep it as a supplement for Berlin.
        yorck_task = source_yorck.fetch_events(days=days)
        cinema_tasks = {
            region: source_kinoheld.fetch_events(
                region=region,
                lat=cfg["lat"],
                lon=cfg["lon"],
                distance_km=cfg["radius_km"],
                days=days,
            )
            for region, cfg in CINEMA_REGIONS.items()
        }

        results = await asyncio.gather(
            kdb_task, tm_task, yorck_task,
            *cinema_tasks.values(),
            return_exceptions=True,
        )
        kdb_events, tm_events, yorck_events = results[0], results[1], results[2]
        cinema_events = dict(zip(cinema_tasks.keys(), results[3:]))

        store = get_store()
        total = 0

        if isinstance(kdb_events, list):
            kdb_events = enrich.apply_heuristic(kdb_events)
            total += store.replace_source(
                source_kdb.REGION, source_kdb.SOURCE_ID, kdb_events
            )
        else:
            logger.exception(
                "events.refresh: kulturdaten fetch failed", exc_info=kdb_events,
            )

        if isinstance(tm_events, list):
            tm_events = enrich.apply_heuristic_if_missing(tm_events)
            total += store.replace_source(
                source_ticketmaster.REGION, source_ticketmaster.SOURCE_ID, tm_events
            )
        else:
            logger.exception(
                "events.refresh: ticketmaster fetch failed", exc_info=tm_events,
            )

        if isinstance(yorck_events, list):
            yorck_events = enrich.apply_heuristic_if_missing(yorck_events)
            total += store.replace_source(
                source_yorck.REGION, source_yorck.SOURCE_ID, yorck_events
            )
        else:
            logger.exception(
                "events.refresh: yorck fetch failed", exc_info=yorck_events,
            )

        for region, evs in cinema_events.items():
            if isinstance(evs, list):
                evs = enrich.apply_heuristic_if_missing(evs)
                total += store.replace_source(
                    region, source_kinoheld.SOURCE_ID, evs
                )
            else:
                logger.exception(
                    "events.refresh: kinoheld[%s] fetch failed",
                    region, exc_info=evs,
                )

        store.delete_before((date.today() - timedelta(days=1)).isoformat())
        logger.info("events.refresh: stored %d events", total)

    if enrich.ai_enabled():
        asyncio.create_task(_ai_enrich_pending())
    return total


# Backwards-compat alias for callers / tests that still use the old name.
refresh_berlin = refresh


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
            await refresh()
        except Exception:
            logger.exception("events.background_refresher: refresh failed")
        await asyncio.sleep(interval_seconds)
