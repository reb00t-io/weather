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
# TM returns labels in the requested locale, so both English and German
# names need to be recognised. Genre wins over segment when present
# (refines "Arts & Theatre" into stage vs art vs festival vs family).
_GENRE_TO_CATEGORY = {
    # stage
    "Theatre": "stage", "Theater": "stage",
    "Comedy": "stage",
    "Dance": "stage", "Tanz": "stage",
    "Performance": "stage",
    "Cabaret": "stage", "Kabarett": "stage",
    "Varieté": "stage", "Variety": "stage",
    "Magic & Illusion": "stage", "Zauberei": "stage",
    "Musical": "stage",
    # art / film
    "Film": "art", "Cinema": "art", "Kino": "art",
    "Visual Arts": "art", "Bildende Kunst": "art",
    "Fine Art": "art",
    "Multimedia": "art",
    # festival
    "Festivals": "festival", "Festival": "festival",
    # family — appears under "Verschiedenes" segment, not under a Family segment
    "Familie": "family", "Family": "family",
    "Children's Theatre": "family", "Kindertheater": "family",
}

_SEGMENT_TO_CATEGORY = {
    "Music": "music", "Musik": "music",
    "Sports": "sports", "Sport": "sports",
    "Arts & Theatre": "stage", "Kunst & Theater": "stage",
    "Family": "family", "Familie": "family",
    "Miscellaneous": "other", "Verschiedenes": "other",
    "Film": "art",
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


def _dedup_key(raw: dict) -> tuple | None:
    """Compute a stable per-show key for collapsing ticket-tier duplicates.

    Ticketmaster lists "ROSALÍA: LUX TOUR 2026", "ROSALÍA … | VIP Packages",
    and "ROSALÍA … | Logen-Seat in der Ticketmaster Suite" as three distinct
    events with different IDs but the same artist/date/venue. Group them
    by (primary attraction id, local date, venue id). Returns None if any
    component is missing — caller falls back to the event's own id."""
    embedded = raw.get("_embedded") or {}
    attractions = embedded.get("attractions") or []
    venues = embedded.get("venues") or []
    start_date = ((raw.get("dates") or {}).get("start") or {}).get("localDate")
    if not (attractions and venues and start_date):
        return None
    return (
        attractions[0].get("id") or "",
        start_date,
        venues[0].get("id") or "",
    )


def _dedup(raw_events: list[dict]) -> list[dict]:
    """Collapse ticket-tier duplicates. Within each group, keep the entry
    whose name has no '|' (the base ticket); fall back to the shortest
    name. Events without a usable dedup key pass through unchanged."""
    groups: dict[tuple, list[dict]] = {}
    passthrough: list[dict] = []
    for raw in raw_events:
        key = _dedup_key(raw)
        if key is None:
            passthrough.append(raw)
        else:
            groups.setdefault(key, []).append(raw)
    out = list(passthrough)
    for items in groups.values():
        if len(items) == 1:
            out.append(items[0])
            continue
        # Prefer base ticket (no "|"); break ties by shortest name.
        items.sort(key=lambda e: ("|" in (e.get("name") or ""), len(e.get("name") or "")))
        out.append(items[0])
    return out


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
        all_raw: list[dict] = []
        for page in range(MAX_PAGES):
            params = {**params_base, "page": str(page)}
            r = await client.get(f"{TM_BASE}/events.json", params=params)
            if r.status_code == 401:
                logger.warning("events.tm.fetch: 401 — bad TICKETMASTER_API_KEY")
                return []
            r.raise_for_status()
            payload = r.json()
            embedded = payload.get("_embedded") or {}
            all_raw.extend(embedded.get("events") or [])
            page_info = payload.get("page") or {}
            if page + 1 >= page_info.get("totalPages", 0):
                break

        # Dedup ticket-tier duplicates first (groups by attraction+date+venue),
        # then dedup by id (catches occasional cross-page repeats).
        deduped = _dedup(all_raw)
        seen_ids = set()
        out: list[Event] = []
        for raw in deduped:
            ev = _normalise(raw, refreshed_at=ts)
            if ev is None or ev.id in seen_ids:
                continue
            seen_ids.add(ev.id)
            out.append(ev)
        logger.info(
            "events.tm.fetch: %d events after dedup (raw %d)",
            len(out), len(all_raw),
        )
        return out
    finally:
        if own_client:
            await client.aclose()
