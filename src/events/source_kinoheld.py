"""Kinoheld GraphQL source — comprehensive German cinema coverage.

Kinoheld aggregates ticketing for cinemas across Germany. Their public
GraphQL endpoint (next-live.kinoheld.de/graphql) exposes:
- `cinemas(proximity)`: every cinema within a radius of a lat/lon, with
  geocoordinates, slug, and the city it sits in
- `shows(cinemaId, dates)`: the program for a single cinema across a
  list of ISO dates, with the movie denormalised inline (title, country,
  cast, poster) — so we can render a useful card without a second hop

We refresh per region: the caller passes a region label (used for the
event row's `region` column) and a (lat, lon, radius_km) to scope the
proximity search. One Event per (movie, cinema, day) — the earliest
showtime wins as start_time, so the card collapses across showtimes
the same way the Yorck source did before.

No API key. Schema is undocumented; we keep the queries conservative
(only fields we've verified) and tolerate partial responses so a
single cinema or movie failure doesn't poison the whole region.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

ENDPOINT = "https://next-live.kinoheld.de/graphql"
SOURCE_ID = "kinoheld"

# Per-cinema shows() requests run in parallel inside one region; cap kept
# conservative so we don't look like a scraper.
_SHOWS_CONCURRENCY = 8

# GraphQL `cinemas` is paginated with a hard cap of 100 per page.
_PAGE_SIZE = 100


_CINEMAS_QUERY = """
query Cinemas($lat: Float!, $lon: Float!, $distance: Int!, $page: Int!, $perPage: Int!) {
  cinemas(
    proximity: { location: { latitude: $lat, longitude: $lon }, distance: $distance },
    first: $perPage,
    page: $page
  ) {
    paginatorInfo { hasMorePages currentPage }
    data {
      id
      name
      urlSlug
      latitude
      longitude
      isClosed
      isHidden
      city { name urlSlug }
    }
  }
}
"""


_SHOWS_QUERY = """
query Shows($cinemaId: ID!, $dates: [Date!]) {
  shows(cinemaId: $cinemaId, dates: $dates) {
    data {
      beginning
      movie {
        id
        title
        titleOriginal
        urlSlug
        duration
        description
        productionCountries { name }
        actors { name }
        thumbnailImage { url }
        trailers { format url remoteVideoId }
      }
    }
  }
}
"""


async def _gql(
    client: httpx.AsyncClient, query: str, variables: dict,
) -> dict:
    """One GraphQL POST. Returns the `data` block; logs and returns {}
    on transport or query errors (callers tolerate partial data)."""
    try:
        r = await client.post(
            ENDPOINT,
            json={"query": query, "variables": variables},
            headers={"Accept-Language": "de"},
        )
        r.raise_for_status()
    except httpx.HTTPError:
        logger.exception("kinoheld.gql: HTTP error (vars=%s)", variables)
        return {}
    payload = r.json()
    if payload.get("errors"):
        logger.warning("kinoheld.gql: errors %s", payload["errors"])
    return payload.get("data") or {}


async def _fetch_cinemas(
    client: httpx.AsyncClient, *, lat: float, lon: float, distance_km: int,
) -> list[dict]:
    """Paginate cinemas() until exhausted. Filters out closed/hidden rows
    so we don't try to fetch shows for places that are dark."""
    out: list[dict] = []
    page = 1
    while True:
        data = await _gql(client, _CINEMAS_QUERY, {
            "lat": lat, "lon": lon,
            "distance": distance_km,
            "page": page, "perPage": _PAGE_SIZE,
        })
        wrap = data.get("cinemas") or {}
        rows = wrap.get("data") or []
        out.extend(rows)
        info = wrap.get("paginatorInfo") or {}
        if not info.get("hasMorePages"):
            break
        page += 1
        if page > 20:  # 2000 cinemas is well past any sane radius
            logger.warning("kinoheld.cinemas: page cap hit at 20")
            break
    return [c for c in out if not (c.get("isClosed") or c.get("isHidden"))]


async def _fetch_shows(
    client: httpx.AsyncClient, cinema_id: str, dates: list[str],
) -> list[dict]:
    data = await _gql(client, _SHOWS_QUERY, {
        "cinemaId": cinema_id, "dates": dates,
    })
    return ((data.get("shows") or {}).get("data")) or []


def _format_country(countries: list[dict]) -> str | None:
    """Render `[{name: 'Germany'}, {name: 'France'}]` → 'Germany, France'.
    Drops blanks; returns None if everything's empty."""
    names = [c.get("name") for c in (countries or []) if c.get("name")]
    if not names:
        return None
    # Cap at three so 'France, Germany, USA, Lebanon, Qatar' doesn't
    # blow out the meta line on the card.
    return ", ".join(names[:3])


def _format_actors(actors: list[dict], *, limit: int = 5) -> str | None:
    names = [a.get("name") for a in (actors or []) if a.get("name")]
    if not names:
        return None
    return ", ".join(names[:limit])


def _clean_description(text: str | None) -> str | None:
    """Kinoheld returns the synopsis as plain text usually, but we strip
    a few HTML artefacts that occasionally sneak through (`<br>`, double
    whitespace) so the card paragraph reads cleanly."""
    if not isinstance(text, str):
        return None
    s = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = " ".join(s.split())
    return s.strip() or None


def _pick_trailer_url(trailers: list[dict]) -> str | None:
    """First YouTube trailer wins (most reliable embed), then the first
    trailer of any format. We return the canonical watch URL — the
    frontend extracts the YouTube ID for the inline embed."""
    if not trailers:
        return None
    for t in trailers:
        fmt = (t.get("format") or "").upper()
        url = t.get("url")
        if fmt == "YOUTUBE" and isinstance(url, str) and url.strip():
            return url.strip()
    for t in trailers:
        url = t.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _normalise_show(
    show: dict, cinema: dict, *, region: str, refreshed_at: float,
) -> Event | None:
    """Turn one `shows.data[]` row into an Event. Returns None if the
    show is missing the bare-minimum fields (date, movie id, title,
    cinema id+name)."""
    beginning = show.get("beginning")
    if not isinstance(beginning, str):
        return None
    try:
        dt = datetime.fromisoformat(beginning)
    except ValueError:
        return None
    movie = show.get("movie") or {}
    movie_id = movie.get("id")
    title = (movie.get("title") or "").strip()
    if not movie_id or not title:
        return None
    cinema_id = cinema.get("id")
    cinema_name = (cinema.get("name") or "").strip()
    if not cinema_id or not cinema_name:
        return None

    day = dt.strftime("%Y-%m-%d")
    start_time = dt.strftime("%H:%M:%S")

    cinema_slug = (cinema.get("urlSlug") or "").strip()
    city_slug = ((cinema.get("city") or {}).get("urlSlug") or "").strip()
    venue_url = (
        f"https://www.kinoheld.de/kino/{city_slug}/{cinema_slug}"
        if cinema_slug and city_slug else None
    )

    venue_lat = cinema.get("latitude")
    venue_lon = cinema.get("longitude")

    image_url = (movie.get("thumbnailImage") or {}).get("url")
    if isinstance(image_url, str) and not image_url.strip():
        image_url = None

    return Event(
        id=f"kh_{movie_id}_{cinema_id}_{day}",
        region=region,
        start_date=day,
        end_date=day,
        start_time=start_time,
        end_time=None,
        title=title,
        venue=cinema_name,
        is_free=False,
        source=SOURCE_ID,
        refreshed_at=refreshed_at,
        category="film",
        interest_score=2,
        is_civic=False,
        venue_url=venue_url,
        venue_lat=float(venue_lat) if isinstance(venue_lat, (int, float)) else None,
        venue_lon=float(venue_lon) if isinstance(venue_lon, (int, float)) else None,
        image_url=image_url,
        actors=_format_actors(movie.get("actors") or []),
        country=_format_country(movie.get("productionCountries") or []),
        synopsis=_clean_description(movie.get("description")),
        trailer_url=_pick_trailer_url(movie.get("trailers") or []),
    )


def _collapse_per_movie_cinema_day(events: list[Event]) -> list[Event]:
    """Multiple showtimes of the same movie in the same cinema on the same
    day collapse to one Event carrying the earliest start_time. Caller
    invariant: every event in `events` already has category='film' and a
    `kh_<movie>_<cinema>_<day>` id."""
    by_id: dict[str, Event] = {}
    for ev in events:
        prev = by_id.get(ev.id)
        if prev is None:
            by_id[ev.id] = ev
            continue
        # Keep the earliest non-null start_time; otherwise newest wins.
        if ev.start_time and (prev.start_time is None or ev.start_time < prev.start_time):
            by_id[ev.id] = ev
    return list(by_id.values())


async def fetch_events(
    *,
    region: str,
    lat: float,
    lon: float,
    distance_km: int = 35,
    days: int = 14,
    client: httpx.AsyncClient | None = None,
) -> list[Event]:
    """Fetch every (movie, cinema, day) film event around (lat, lon) for
    the next `days` days. `region` is the label written to each event's
    region column (typically the city name we're refreshing for)."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "WeatherApp/1.0 (https://weather.reb00t.io)"},
        )
    try:
        cinemas = await _fetch_cinemas(
            client, lat=lat, lon=lon, distance_km=distance_km,
        )
        if not cinemas:
            logger.info("kinoheld.fetch[%s]: no cinemas in %dkm", region, distance_km)
            return []

        today = date.today()
        dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
        ts = now_ts()

        sem = asyncio.Semaphore(_SHOWS_CONCURRENCY)

        async def fetch_for(cinema: dict) -> list[Event]:
            async with sem:
                shows = await _fetch_shows(client, cinema["id"], dates)
            return [
                ev for ev in (
                    _normalise_show(s, cinema, region=region, refreshed_at=ts)
                    for s in shows
                ) if ev is not None
            ]

        per_cinema = await asyncio.gather(*(fetch_for(c) for c in cinemas))
        flat = [ev for sub in per_cinema for ev in sub]
        events = _collapse_per_movie_cinema_day(flat)
        logger.info(
            "kinoheld.fetch[%s]: %d events, %d cinemas, %d shows raw",
            region, len(events), len(cinemas), len(flat),
        )
        return events
    finally:
        if own_client:
            await client.aclose()
