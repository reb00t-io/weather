"""kulturdaten.berlin event source.

Public API at https://api-v2.kulturdaten.berlin/api — no key required, CC BY 3.0.
We POST to /events/search with a date-range filter, paginate at the 500-event
maximum, and normalise each row to the local `Event` dataclass.

The event payload references attractions/locations rather than embedding them,
but the response includes `referenceLabel.de` for both — enough for a list view.
Description, website, category would require a per-attraction fetch and are
deferred until v2.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

KDB_BASE = "https://api-v2.kulturdaten.berlin/api"
PAGE_SIZE = 500       # API maximum
MAX_PAGES = 10        # safety cap; > 5000 events in 14 days would be unusual
SOURCE_ID = "kulturdaten.berlin"
REGION = "Berlin"     # this source only covers Berlin


def _clean_time(t: str | None) -> str | None:
    """All-day rows come through as '00:00:00' for both start and end.
    Treat that as 'no time' so the UI can render 'den ganzen Tag'."""
    if not t or t == "00:00:00":
        return None
    return t


def _clean_venue(label: str | None) -> str | None:
    """Some 'locations' are URLs (online participation events).
    Render those as 'Online' rather than dumping a URL into a card."""
    if not label:
        return None
    if label.startswith(("http://", "https://")):
        return "Online"
    return label.strip() or None


def _normalise(raw: dict, *, refreshed_at: float) -> Event | None:
    """Map a kulturdaten event dict to our local Event. Returns None if the
    row is missing the fields we depend on (rare, but defensive)."""
    sched = raw.get("schedule") or {}
    start_date = sched.get("startDate")
    if not start_date:
        return None

    attractions = raw.get("attractions") or []
    title = None
    if attractions:
        label = (attractions[0].get("referenceLabel") or {})
        title = label.get("de") or label.get("en")
    if not title:
        return None

    locations = raw.get("locations") or []
    venue = None
    if locations:
        label = (locations[0].get("referenceLabel") or {})
        venue = _clean_venue(label.get("de") or label.get("en"))

    admission = raw.get("admission") or {}
    is_free = admission.get("ticketType") == "ticketType.freeOfCharge"

    identifier = raw.get("identifier")
    if not identifier:
        return None

    # All-day detection: both start and end are 00:00:00.
    raw_start = sched.get("startTime")
    raw_end = sched.get("endTime")
    if raw_start == "00:00:00" and raw_end == "00:00:00":
        start_time = end_time = None
    else:
        start_time = _clean_time(raw_start)
        end_time = _clean_time(raw_end)

    return Event(
        id=identifier,
        region=REGION,
        start_date=start_date,
        end_date=sched.get("endDate"),
        start_time=start_time,
        end_time=end_time,
        title=title.strip(),
        venue=venue,
        is_free=is_free,
        source=SOURCE_ID,
        refreshed_at=refreshed_at,
    )


async def fetch_events(
    *,
    start: date | None = None,
    days: int = 14,
    client: httpx.AsyncClient | None = None,
) -> list[Event]:
    """Fetch normalised events for the next `days` days starting at `start`
    (defaults to today). Status is filtered to published events only."""
    if start is None:
        start = date.today()
    end = start + timedelta(days=days)

    body = {
        "searchFilter": {
            "schedule.startDate": {
                "$gte": start.isoformat(),
                "$lte": end.isoformat(),
            },
            "status": "event.published",
        }
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        ts = now_ts()
        out: list[Event] = []
        for page in range(1, MAX_PAGES + 1):
            url = f"{KDB_BASE}/events/search?page={page}&pageSize={PAGE_SIZE}"
            r = await client.post(url, json=body)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or {}
            events = data.get("events") or []
            for raw in events:
                ev = _normalise(raw, refreshed_at=ts)
                if ev is not None:
                    out.append(ev)
            if len(events) < PAGE_SIZE:
                break
        else:
            logger.warning(
                "kulturdaten fetch hit MAX_PAGES=%d cap; some events skipped",
                MAX_PAGES,
            )
        return out
    finally:
        if own_client:
            await client.aclose()
