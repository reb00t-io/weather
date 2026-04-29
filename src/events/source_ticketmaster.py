"""Ticketmaster Discovery API source.

Adds music/sports/large-venue coverage that kulturdaten.berlin's
Bezirkskalender systematically misses (clubs, arenas, commercial
theatre). Auth via TICKETMASTER_API_KEY (free at developer.ticketmaster.com).
The source self-disables when the key is unset — useful in dev/CI.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

TM_BASE = "https://app.ticketmaster.com/discovery/v2"
SOURCE_ID = "ticketmaster"
REGION = "Berlin"

# Berlin city center; radius covers all Berlin venues plus near-suburbs.
BERLIN_LAT = 52.520008
BERLIN_LON = 13.404954
RADIUS_KM = 30

PAGE_SIZE = 200       # API default cap
MAX_PAGES = 5         # safety cap; > 1000 events in 14 days would be unusual

# Map Ticketmaster (segment, genre) → our category enum.
# Segment is always present on commercial events; genre refines stage vs art.
_GENRE_TO_CATEGORY = {
    "Theatre":      "stage",
    "Comedy":       "stage",
    "Dance":        "stage",
    "Performance":  "stage",
    "Cabaret":      "stage",
    "Magic & Illusion": "stage",
    "Film":         "art",
    "Cinema":       "art",
    "Visual Arts":  "art",
    "Fine Art":     "art",
    "Multimedia":   "art",
    "Festivals":    "festival",
    "Festival":     "festival",
}

_SEGMENT_TO_CATEGORY = {
    "Music":          "music",
    "Sports":         "sports",
    "Arts & Theatre": "stage",   # default; genre may refine to art/festival
    "Family":         "family",
    "Miscellaneous":  "other",
    "Film":           "art",
}


def _classify(classifications: list[dict] | None) -> str:
    """Pick our category from the first primary classification."""
    if not classifications:
        return "other"
    primary = next(
        (c for c in classifications if c.get("primary")),
        classifications[0],
    )
    genre_name = (primary.get("genre") or {}).get("name") or ""
    if genre_name in _GENRE_TO_CATEGORY:
        return _GENRE_TO_CATEGORY[genre_name]
    segment_name = (primary.get("segment") or {}).get("name") or ""
    return _SEGMENT_TO_CATEGORY.get(segment_name, "other")


def _interest_score(category: str, name: str) -> int:
    """Heuristic interest score for TM events. They're all commercial draws,
    so the floor is 2; festivals and 'big' indicators bump to 3."""
    if category == "festival":
        return 3
    n = name.lower()
    if "festival" in n or "tour" in n or "live" in n[:8]:
        return 3
    return 2


def _normalise(raw: dict, *, refreshed_at: float) -> Event | None:
    """Map a TM event to our local Event. Returns None if missing required
    fields (a date or a name)."""
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    dates = raw.get("dates") or {}
    start = dates.get("start") or {}
    start_date = start.get("localDate")
    if not start_date:
        return None
    # `dateTBA: true` means "event scheduled but date may shift" — we still
    # show it. `noSpecificTime` and `timeTBA` mean we have no clock time.
    has_time = not (start.get("noSpecificTime") or start.get("timeTBA"))
    start_time = start.get("localTime") if has_time else None

    venue_name = None
    embedded = raw.get("_embedded") or {}
    venues = embedded.get("venues") or []
    if venues:
        venue_name = (venues[0].get("name") or "").strip() or None

    classifications = raw.get("classifications") or []
    category = _classify(classifications)
    score = _interest_score(category, name)

    tm_id = raw.get("id")
    if not tm_id:
        return None

    return Event(
        id=f"tm_{tm_id}",
        region=REGION,
        start_date=start_date,
        end_date=start_date,
        start_time=start_time,
        end_time=None,
        title=name,
        venue=venue_name,
        is_free=False,            # commercial; price ranges always present in practice
        source=SOURCE_ID,
        refreshed_at=refreshed_at,
        category=category,
        interest_score=score,
        is_civic=False,
    )


async def fetch_events(
    *,
    start: date | None = None,
    days: int = 14,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[Event]:
    """Fetch normalised events from Ticketmaster for Berlin over the next
    `days` days. Returns [] when no API key is configured."""
    api_key = api_key or os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        logger.info("events.tm.fetch: TICKETMASTER_API_KEY unset; skipping")
        return []

    if start is None:
        start = date.today()
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(
        start + timedelta(days=days), time.max, tzinfo=timezone.utc
    )

    params_base = {
        "apikey": api_key,
        "latlong": f"{BERLIN_LAT},{BERLIN_LON}",
        "radius": str(RADIUS_KM),
        "unit": "km",
        "startDateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "locale": "de-de,en",
        "size": str(PAGE_SIZE),
        "sort": "date,asc",
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        ts = now_ts()
        out: list[Event] = []
        for page in range(MAX_PAGES):
            params = {**params_base, "page": str(page)}
            r = await client.get(f"{TM_BASE}/events.json", params=params)
            if r.status_code == 401:
                logger.warning("events.tm.fetch: 401 — bad TICKETMASTER_API_KEY")
                return []
            r.raise_for_status()
            payload = r.json()
            embedded = payload.get("_embedded") or {}
            raw_events = embedded.get("events") or []
            for raw in raw_events:
                ev = _normalise(raw, refreshed_at=ts)
                if ev is not None:
                    out.append(ev)
            page_info = payload.get("page") or {}
            total_pages = page_info.get("totalPages", 0)
            if page + 1 >= total_pages:
                break
        # De-duplicate: TM occasionally returns the same event on multiple
        # pages near a totalPages edge. Keep the first occurrence.
        seen = set()
        unique = []
        for e in out:
            if e.id in seen:
                continue
            seen.add(e.id)
            unique.append(e)
        logger.info("events.tm.fetch: %d unique events", len(unique))
        return unique
    finally:
        if own_client:
            await client.aclose()
