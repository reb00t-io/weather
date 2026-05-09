"""Yorck Kinos cinema source for Berlin.

The Yorck Kinogruppe runs 14 cinemas across Berlin (delphi LUX, Kant Kino,
Filmtheater am Friedrichshain, Rollberg, Babylon Kreuzberg, Kino
International, etc.) and ships the full programme as a Next.js
__NEXT_DATA__ JSON blob embedded in the public /films page. The /kinos
page exposes the cinema directory (slug + coordinates) in the same blob.

We fetch both, then emit one Event per (film, cinema, day) carrying the
cinema's slug-derived program URL and coordinates so the frontend can
sort cinemas for a given film by distance from the user.

No API key, no rate limit signalled. Two GETs per refresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

YORCK_FILMS_URL = "https://www.yorck.de/films"
YORCK_CINEMAS_URL = "https://www.yorck.de/kinos"
YORCK_CINEMA_PAGE = "https://www.yorck.de/kinos/{slug}"
YORCK_FILM_PAGE = "https://www.yorck.de/filme/{slug}"
SOURCE_ID = "yorck"
REGION = "Berlin"

# Cap for parallel detail-page fetches. Yorck's site is happy with bursts
# of a handful of requests; this keeps the refresh pass under ~15s.
_DETAIL_CONCURRENCY = 8

# Contentful image transform: thumbnail-sized webp tuned for the movie
# card. Card is ~96px wide on mobile; 2× = 192px, but 480px gives some
# headroom for high-DPI screens without being wasteful.
_IMAGE_TRANSFORM = "w=480&fm=webp&q=80"

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


def _cinema_slug(name: str) -> str:
    """Fallback slug if the cinema directory didn't yield one — strip
    diacritics, lowercase, hyphenate."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _cinemas_from_directory(data: dict) -> dict[str, dict]:
    """Parse the /kinos page payload into name → {slug, lat, lon, url}.

    The cinema name is the join key with the films feed (which only
    carries the cinema's display name)."""
    cinemas = ((data.get("props") or {}).get("pageProps") or {}).get(
        "cinemas"
    ) or []
    out: dict[str, dict] = {}
    for c in cinemas:
        f = c.get("fields") or {}
        name = (f.get("name") or "").strip()
        slug = (f.get("slug") or "").strip() or _cinema_slug(name)
        coords = f.get("coordinates") or {}
        lat = coords.get("lat")
        lon = coords.get("lon")
        if not name:
            continue
        out[name] = {
            "slug": slug,
            "lat": float(lat) if isinstance(lat, (int, float)) else None,
            "lon": float(lon) if isinstance(lon, (int, float)) else None,
            "url": YORCK_CINEMA_PAGE.format(slug=slug),
        }
    return out


def _image_url_from_film(film: dict) -> str | None:
    """Pull the listing-page poster URL (heroImage) and append a Contentful
    image-API transform so we serve a thumbnail-sized webp instead of the
    raw 4K source."""
    fields = film.get("fields") or {}
    hero = fields.get("heroImage") or {}
    img = ((hero.get("fields") or {}).get("image") or {}).get("fields") or {}
    file = img.get("file") or {}
    url = file.get("url")
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{_IMAGE_TRANSFORM}"


def _normalise_film_cinema_day(
    film: dict,
    cinema_name: str,
    showings: list[dict],
    *,
    refreshed_at: float,
    promoted_slugs: set[str],
    cinema_directory: dict[str, dict],
    actors: str | None = None,
    image_url: str | None = None,
    synopsis: str | None = None,
    trailer_url: str | None = None,
) -> Event | None:
    """Build a single Event from a film's same-day, same-cinema sessions.
    Showings is a non-empty list of session dicts on the same date at the
    same cinema; the earliest start time becomes the card's start_time."""
    fields = film.get("fields") or {}
    title = (fields.get("title") or "").strip()
    if not title:
        return None
    film_sys_id = (film.get("sys") or {}).get("id") or fields.get("slug")
    if not film_sys_id:
        return None
    slug = fields.get("slug")

    parsed_times: list[tuple[str, str]] = []
    for s in showings:
        dt = _session_date_time(s)
        if dt is not None:
            parsed_times.append(dt)
    if not parsed_times:
        return None
    parsed_times.sort()
    day = parsed_times[0][0]
    earliest_time = parsed_times[0][1]

    info = cinema_directory.get(cinema_name) or {}
    cinema_slug = info.get("slug") or _cinema_slug(cinema_name)
    venue_url = info.get("url") or YORCK_CINEMA_PAGE.format(slug=cinema_slug)
    venue_lat = info.get("lat")
    venue_lon = info.get("lon")

    score = 3 if (slug and slug in promoted_slugs) else 2

    return Event(
        id=f"yorck_{film_sys_id}_{cinema_slug}_{day}",
        region=REGION,
        start_date=day,
        end_date=day,
        start_time=earliest_time,
        end_time=None,
        title=title,
        venue=cinema_name,
        is_free=False,
        source=SOURCE_ID,
        refreshed_at=refreshed_at,
        category="film",
        interest_score=score,
        is_civic=False,
        venue_url=venue_url,
        venue_lat=venue_lat,
        venue_lon=venue_lon,
        image_url=image_url,
        actors=actors,
        synopsis=synopsis,
        trailer_url=trailer_url,
    )


def _events_in_window(
    data: dict,
    *,
    start: date,
    days: int,
    refreshed_at: float,
    cinema_directory: dict[str, dict] | None = None,
    film_metadata: dict[str, dict] | None = None,
) -> list[Event]:
    """Walk the parsed __NEXT_DATA__ blob, group sessions by (film, cinema,
    day), restrict to [start, start+days), and emit one Event per group.
    Each event carries the cinema's slug/url/coords plus the film's
    poster/cast so the UI can rank cinemas by distance and render a card
    that's useful before tapping."""
    end = start + timedelta(days=days)
    promoted = _promoted_film_ids(data)
    directory = cinema_directory or {}
    metadata = film_metadata or {}
    out: list[Event] = []
    for film in _films_from(data):
        fields = film.get("fields") or {}
        slug = fields.get("slug") or ""
        meta = metadata.get(slug) or {}
        actors = meta.get("cast")
        synopsis = meta.get("synopsis")
        trailer_url = meta.get("trailer_url")
        image_url = _image_url_from_film(film)
        sessions = fields.get("sessions") or []
        # Bucket sessions by (date, cinema_name).
        by_day_cinema: dict[tuple[str, str], list[dict]] = {}
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
            cinema = _cinema_name(s)
            if not cinema:
                continue
            by_day_cinema.setdefault((d, cinema), []).append(s)
        for (_, cinema), showings in by_day_cinema.items():
            ev = _normalise_film_cinema_day(
                film,
                cinema,
                showings,
                refreshed_at=refreshed_at,
                promoted_slugs=promoted,
                cinema_directory=directory,
                actors=actors,
                image_url=image_url,
                synopsis=synopsis,
                trailer_url=trailer_url,
            )
            if ev is not None:
                out.append(ev)
    return out


def _slugs_with_sessions_in_window(
    data: dict, *, start: date, end: date,
) -> list[str]:
    """Return the unique film slugs that have at least one session within
    [start, end). Used to bound the per-film detail fetch to films that
    will actually appear in the window."""
    out: list[str] = []
    seen: set[str] = set()
    for film in _films_from(data):
        fields = film.get("fields") or {}
        slug = fields.get("slug")
        if not slug or slug in seen:
            continue
        sessions = fields.get("sessions") or []
        for s in sessions:
            dt = _session_date_time(s)
            if dt is None:
                continue
            try:
                d_obj = datetime.strptime(dt[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            if start <= d_obj < end:
                out.append(slug)
                seen.add(slug)
                break
    return out


async def _fetch_film_metadata(
    client: httpx.AsyncClient, slugs: list[str],
) -> dict[str, dict]:
    """Pull cast, synopsis and trailer ID for each slug from the per-film
    detail page. Returns slug → {'cast': str|None, 'synopsis': str|None,
    'trailer_url': str|None}; failures are silently dropped so events
    still ship without enriched metadata."""
    if not slugs:
        return {}
    sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_one(slug: str) -> tuple[str, dict | None]:
        async with sem:
            try:
                r = await client.get(YORCK_FILM_PAGE.format(slug=slug))
                r.raise_for_status()
            except httpx.HTTPError:
                return slug, None
            data = _parse_next_data(r.text)
            if data is None:
                return slug, None
            fields = (
                ((data.get("props") or {}).get("pageProps") or {})
                .get("film") or {}
            ).get("fields") or {}
            cast = fields.get("cast")
            synopsis = fields.get("synopsis") or fields.get("about")
            yt_id = fields.get("trailer1YouTubeId") or fields.get("trailer2YouTubeId")
            return slug, {
                "cast": cast.strip() if isinstance(cast, str) and cast.strip() else None,
                "synopsis": _clean_yorck_synopsis(synopsis),
                "trailer_url": (
                    f"https://www.youtube.com/watch?v={yt_id.strip()}"
                    if isinstance(yt_id, str) and yt_id.strip() else None
                ),
            }

    results = await asyncio.gather(*(fetch_one(s) for s in slugs))
    return {s: meta for s, meta in results if meta is not None}


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_yorck_synopsis(value) -> str | None:
    """Yorck stores the synopsis as a Contentful Rich Text document
    (nested `nodeType` blocks) on most films, but sometimes serialises
    it to plain HTML string. Extract a single paragraph either way."""
    if isinstance(value, str):
        text = _HTML_TAG_RE.sub(" ", value)
        return " ".join(text.split()).strip() or None
    if not isinstance(value, dict):
        return None
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("nodeType") == "text":
                t = node.get("value")
                if isinstance(t, str):
                    out.append(t)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    text = " ".join("".join(out).split()).strip()
    return text or None


async def _fetch_cinema_directory(
    client: httpx.AsyncClient,
) -> dict[str, dict]:
    """Hit /kinos once and return a name → {slug, lat, lon, url} map.
    On failure we return {} — events still ship, just without coords/URL,
    and the frontend falls back to a name-only render."""
    try:
        r = await client.get(YORCK_CINEMAS_URL)
        r.raise_for_status()
        data = _parse_next_data(r.text)
        if data is None:
            logger.warning("events.yorck.cinemas: no __NEXT_DATA__ in response")
            return {}
        return _cinemas_from_directory(data)
    except httpx.HTTPError:
        logger.exception("events.yorck.cinemas: HTTP error")
        return {}


async def fetch_events(
    *,
    start: date | None = None,
    days: int = 14,
    client: httpx.AsyncClient | None = None,
) -> list[Event]:
    """Fetch the Yorck programme and emit one Event per (film, cinema, day)
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
        cinema_directory = await _fetch_cinema_directory(client)
        r = await client.get(YORCK_FILMS_URL)
        r.raise_for_status()
        data = _parse_next_data(r.text)
        if data is None:
            logger.warning("events.yorck.fetch: no __NEXT_DATA__ in response")
            return []
        slugs = _slugs_with_sessions_in_window(
            data, start=start, end=start + timedelta(days=days),
        )
        film_metadata = await _fetch_film_metadata(client, slugs)
        events = _events_in_window(
            data,
            start=start,
            days=days,
            refreshed_at=now_ts(),
            cinema_directory=cinema_directory,
            film_metadata=film_metadata,
        )
        logger.info(
            "events.yorck.fetch: %d film-cinema-day events "
            "(%d cinemas, %d films enriched / %d slugs)",
            len(events), len(cinema_directory),
            len(film_metadata), len(slugs),
        )
        return events
    except httpx.HTTPError:
        logger.exception("events.yorck.fetch: HTTP error")
        return []
    finally:
        if own_client:
            await client.aclose()
