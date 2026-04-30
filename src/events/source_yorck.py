"""Yorck Kinos cinema source for Berlin.

The Yorck Kinogruppe runs 14 cinemas across Berlin (delphi LUX, Kant Kino,
Filmtheater am Friedrichshain, Rollberg, Babylon Kreuzberg, Kino
International, etc.) and ships the full programme as a Next.js
__NEXT_DATA__ JSON blob embedded in the public /films page. We extract
that, group sessions by (film, date) within the standard 14-day window,
and emit one Event per (film, day) — a film showing in two cinemas on
the same day still becomes one card so the tab doesn't drown.

No API key, no rate limit signalled, served as part of the regular page
fetch. License/usage: kept conservative — one fetch per refresh cycle.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

YORCK_URL = "https://www.yorck.de/films"
SOURCE_ID = "yorck"
REGION = "Berlin"

# Anchor of the embedded JSON blob.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)


def _parse_next_data(html: str) -> dict | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.exception("yorck: __NEXT_DATA__ parse failed")
        return None


def _films_from(data: dict) -> list[dict]:
    return ((data.get("props") or {}).get("pageProps") or {}).get("films") or []


def _promoted_film_ids(data: dict) -> set[str]:
    """Yorck-curated picks (top5PromotedSpecials). Earn an interest_score
    bump from 2 to 3."""
    promoted = ((data.get("props") or {}).get("pageProps") or {}).get(
        "top5PromotedSpecials"
    ) or []
    out = set()
    for p in promoted:
        slug = p.get("slug")
        if slug:
            out.add(slug)
    return out


def _session_date_time(session: dict) -> tuple[str, str] | None:
    """Pull (YYYY-MM-DD, HH:MM:SS) from a session field. Returns None
    if startTime is missing or malformed."""
    fields = session.get("fields") or {}
    raw = fields.get("startTime")
    if not raw:
        return None
    try:
        # Yorck uses ISO 8601 with timezone, e.g. 2026-05-05T17:30:00+01:00.
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")


def _cinema_name(session: dict) -> str | None:
    fields = session.get("fields") or {}
    cinema = (fields.get("cinema") or {}).get("fields") or {}
    name = cinema.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _format_venue(cinemas: list[str]) -> str:
    """Render the venue label for a film-day card.

    One cinema  → 'delphi LUX'
    Two cinemas → 'delphi LUX + Kant Kino'
    Three+      → 'delphi LUX +2 Kinos'
    """
    uniq = []
    for c in cinemas:
        if c and c not in uniq:
            uniq.append(c)
    if not uniq:
        return ""
    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) == 2:
        return f"{uniq[0]} + {uniq[1]}"
    return f"{uniq[0]} +{len(uniq) - 1} Kinos"


def _normalise_film_day(
    film: dict,
    showings: list[dict],
    *,
    refreshed_at: float,
    promoted_slugs: set[str],
) -> Event | None:
    """Build a single Event from a film's same-day sessions. Showings is
    a non-empty list of session dicts on the same date."""
    fields = film.get("fields") or {}
    title = (fields.get("title") or "").strip()
    if not title:
        return None
    film_sys_id = (film.get("sys") or {}).get("id") or fields.get("slug")
    if not film_sys_id:
        return None
    slug = fields.get("slug")

    # Sort sessions by time so the card shows the earliest start.
    parsed = []
    for s in showings:
        dt = _session_date_time(s)
        if dt is None:
            continue
        parsed.append((dt[0], dt[1], _cinema_name(s)))
    if not parsed:
        return None
    parsed.sort()
    day = parsed[0][0]
    earliest_time = parsed[0][1]
    cinemas = [c for _, _, c in parsed]
    venue = _format_venue(cinemas)

    score = 3 if (slug and slug in promoted_slugs) else 2

    return Event(
        id=f"yorck_{film_sys_id}_{day}",
        region=REGION,
        start_date=day,
        end_date=day,
        start_time=earliest_time,
        end_time=None,
        title=title,
        venue=venue or None,
        is_free=False,
        source=SOURCE_ID,
        refreshed_at=refreshed_at,
        category="film",
        interest_score=score,
        is_civic=False,
    )


def _events_in_window(
    data: dict,
    *,
    start: date,
    days: int,
    refreshed_at: float,
) -> list[Event]:
    """Walk the parsed __NEXT_DATA__ blob, collapse sessions into one
    Event per (film, day), restrict to [start, start+days)."""
    end = start + timedelta(days=days)
    promoted = _promoted_film_ids(data)
    out: list[Event] = []
    for film in _films_from(data):
        sessions = (film.get("fields") or {}).get("sessions") or []
        # Bucket sessions by date.
        by_day: dict[str, list[dict]] = {}
        for s in sessions:
            dt = _session_date_time(s)
            if dt is None:
                continue
            d = dt[0]
            try:
                d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d_obj < start or d_obj >= end:
                continue
            by_day.setdefault(d, []).append(s)
        for showings in by_day.values():
            ev = _normalise_film_day(
                film, showings,
                refreshed_at=refreshed_at,
                promoted_slugs=promoted,
            )
            if ev is not None:
                out.append(ev)
    return out


async def fetch_events(
    *,
    start: date | None = None,
    days: int = 14,
    client: httpx.AsyncClient | None = None,
) -> list[Event]:
    """Fetch the Yorck programme and emit one Event per (film, day)
    within the next `days` days."""
    if start is None:
        start = date.today()

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "User-Agent": "WeatherApp/1.0 (https://weather.reb00t.io)",
            },
        )
    try:
        r = await client.get(YORCK_URL)
        r.raise_for_status()
        data = _parse_next_data(r.text)
        if data is None:
            logger.warning("events.yorck.fetch: no __NEXT_DATA__ in response")
            return []
        events = _events_in_window(
            data, start=start, days=days, refreshed_at=now_ts()
        )
        logger.info("events.yorck.fetch: %d film-day events", len(events))
        return events
    except httpx.HTTPError:
        logger.exception("events.yorck.fetch: HTTP error")
        return []
    finally:
        if own_client:
            await client.aclose()
