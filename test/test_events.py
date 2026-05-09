"""Tests for the events module: store, source normalisation, and API."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("API_KEY", "test-api-key")

from src.events import enrich as events_enrich  # noqa: E402
from src.events import service as events_service  # noqa: E402
from src.events import source_ticketmaster as tm  # noqa: E402
from src.events import source_yorck as yorck  # noqa: E402
from src.events.api import _resolve_region  # noqa: E402
from src.events.source_kdb import _normalise  # noqa: E402
from src.events.store import Event, EventStore  # noqa: E402
from src.main import API_KEY, app  # noqa: E402

AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture()
def store(tmp_path):
    s = EventStore(db_path=tmp_path / "events.db")
    return s


def _ev(**kw):
    """Build an Event with sensible defaults for tests."""
    defaults = dict(
        id="E_TEST", region="Berlin",
        start_date="2026-04-29", end_date="2026-04-29",
        start_time="20:00:00", end_time="22:00:00",
        title="Demo event", venue="Some venue",
        is_free=True, source="test", refreshed_at=0.0,
    )
    defaults.update(kw)
    return Event(**defaults)


# ── Store ───────────────────────────────────────────────────────────────────

def test_store_upsert_and_query(store):
    store.upsert_many([_ev(id="E1", start_date="2026-04-29")])
    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert len(rows) == 1
    assert rows[0].id == "E1"
    assert rows[0].is_free is True


def test_store_upsert_replaces_by_id(store):
    store.upsert_many([_ev(id="E1", title="Old title")])
    store.upsert_many([_ev(id="E1", title="New title")])
    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert len(rows) == 1
    assert rows[0].title == "New title"


def test_store_query_filters_by_region_and_range(store):
    store.upsert_many([
        _ev(id="A", region="Berlin", start_date="2026-04-29"),
        _ev(id="B", region="Berlin", start_date="2026-05-20"),  # out of range
        _ev(id="C", region="Munich", start_date="2026-04-29"),  # other region
    ])
    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert [r.id for r in rows] == ["A"]


def test_store_orders_timed_before_all_day_same_day(store):
    store.upsert_many([
        _ev(id="ALL", start_time=None, end_time=None, title="All day"),
        _ev(id="EVE", start_time="20:00:00", title="Evening"),
        _ev(id="MORN", start_time="08:00:00", title="Morning"),
    ])
    rows = store.query("Berlin", "2026-04-29", "2026-04-29")
    assert [r.id for r in rows] == ["MORN", "EVE", "ALL"]


def test_store_replace_source_only_touches_its_slice(store):
    """replace_source must keep events from other sources for the same region
    untouched. Without this, refreshing Ticketmaster would wipe kulturdaten."""
    store.upsert_many([
        _ev(id="KDB1", region="Berlin", source="kulturdaten.berlin"),
        _ev(id="KDB2", region="Berlin", source="kulturdaten.berlin"),
        _ev(id="KEEP", region="Munich", source="kulturdaten.berlin"),
        _ev(id="TM_OLD", region="Berlin", source="ticketmaster"),
    ])
    store.replace_source("Berlin", "ticketmaster",
                         [_ev(id="TM_NEW", region="Berlin", source="ticketmaster")])
    berlin = store.query("Berlin", "2026-04-29", "2026-05-13")
    munich = store.query("Munich", "2026-04-29", "2026-05-13")
    assert {r.id for r in berlin} == {"KDB1", "KDB2", "TM_NEW"}
    assert [r.id for r in munich] == ["KEEP"]


def test_store_delete_before(store):
    store.upsert_many([
        _ev(id="OLD", start_date="2026-04-01", end_date="2026-04-01"),
        _ev(id="MULTI", start_date="2026-04-01", end_date="2026-05-01"),
        _ev(id="FUTURE", start_date="2026-04-29", end_date="2026-04-29"),
    ])
    removed = store.delete_before("2026-04-15")
    assert removed == 1  # only OLD; MULTI ends after cutoff
    remaining = {r.id for r in store.query("Berlin", "2026-04-01", "2026-05-13")}
    assert remaining == {"MULTI", "FUTURE"}


# ── Source normalisation ────────────────────────────────────────────────────

def test_normalise_basic_event():
    raw = {
        "identifier": "E_X",
        "schedule": {"startDate": "2026-04-29", "endDate": "2026-04-29",
                     "startTime": "19:00:00", "endTime": "21:00:00"},
        "attractions": [{"referenceLabel": {"de": "  Konzert  "}}],
        "locations": [{"referenceLabel": {"de": "Philharmonie"}}],
        "admission": {"ticketType": "ticketType.paid"},
    }
    ev = _normalise(raw, refreshed_at=42.0)
    assert ev is not None
    assert ev.id == "E_X"
    assert ev.title == "Konzert"  # stripped
    assert ev.venue == "Philharmonie"
    assert ev.start_time == "19:00:00"
    assert ev.is_free is False
    assert ev.refreshed_at == 42.0


def test_normalise_all_day_collapses_zero_times():
    raw = {
        "identifier": "E_AD",
        "schedule": {"startDate": "2026-04-29", "endDate": "2026-04-29",
                     "startTime": "00:00:00", "endTime": "00:00:00"},
        "attractions": [{"referenceLabel": {"de": "Ausstellung"}}],
        "locations": [{"referenceLabel": {"de": "Galerie"}}],
        "admission": {"ticketType": "ticketType.freeOfCharge"},
    }
    ev = _normalise(raw, refreshed_at=0.0)
    assert ev.start_time is None
    assert ev.end_time is None
    assert ev.is_free is True


def test_normalise_url_venue_becomes_online():
    raw = {
        "identifier": "E_URL",
        "schedule": {"startDate": "2026-04-29"},
        "attractions": [{"referenceLabel": {"de": "Online-Beteiligung"}}],
        "locations": [{"referenceLabel": {"de": "https://example.com/foo"}}],
    }
    ev = _normalise(raw, refreshed_at=0.0)
    assert ev.venue == "Online"


def test_normalise_returns_none_on_missing_required():
    # Missing identifier
    assert _normalise({
        "schedule": {"startDate": "2026-04-29"},
        "attractions": [{"referenceLabel": {"de": "x"}}],
    }, refreshed_at=0.0) is None
    # Missing title
    assert _normalise({
        "identifier": "E_X",
        "schedule": {"startDate": "2026-04-29"},
        "attractions": [],
    }, refreshed_at=0.0) is None
    # Missing date
    assert _normalise({
        "identifier": "E_X",
        "schedule": {},
        "attractions": [{"referenceLabel": {"de": "x"}}],
    }, refreshed_at=0.0) is None


# ── Region resolution ──────────────────────────────────────────────────────

def test_resolve_region_strips_district_and_country():
    assert _resolve_region("Berlin-Marzahn, Berlin, Deutschland") == "Berlin"
    assert _resolve_region("Berlin") == "Berlin"
    assert _resolve_region("  Berlin  ") == "Berlin"
    assert _resolve_region("München") == "München"


def test_resolve_region_strips_german_place_name_prefixes():
    """Open-Meteo's geocoder labels Salzwedel as 'Hansestadt Salzwedel'
    and Munich as 'Landeshauptstadt München' — both must reduce to the
    bare city name the sources tag events with, otherwise the events
    tab silently shows nothing for those locations."""
    assert _resolve_region("Hansestadt Salzwedel") == "Salzwedel"
    assert _resolve_region("Hansestadt Salzwedel, Sachsen-Anhalt") == "Salzwedel"
    assert _resolve_region("Landeshauptstadt München") == "München"
    assert _resolve_region("Stadt Köln") == "Köln"


# ── API endpoint ────────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    s = EventStore(db_path=tmp_path / "events.db")
    events_service.set_store(s)
    yield s
    events_service.set_store(None)  # reset


@pytest.fixture()
def client():
    return app.test_client()


async def test_api_requires_auth(client):
    r = await client.get("/api/events?region=Berlin")
    assert r.status_code == 401


async def test_api_requires_region(client):
    r = await client.get("/api/events", headers=AUTH_HEADERS)
    assert r.status_code == 400


async def test_api_returns_empty_for_unknown_region(client, isolated_store):
    r = await client.get("/api/events?region=Atlantis", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["count"] == 0
    assert body["events"] == []


async def test_api_returns_events_filtered_to_window(client, isolated_store):
    today = date.today().isoformat()
    isolated_store.upsert_many([
        _ev(id="E1", start_date=today, title="Today"),
    ])
    r = await client.get("/api/events?region=Berlin&days=14", headers=AUTH_HEADERS)
    body = await r.get_json()
    assert body["count"] == 1
    assert body["events"][0]["title"] == "Today"
    assert body["region"] == "Berlin"


async def test_api_resolves_district_to_region(client, isolated_store):
    today = date.today().isoformat()
    isolated_store.upsert_many([_ev(id="E1", start_date=today, region="Berlin")])
    r = await client.get(
        "/api/events?city=Berlin-Marzahn",
        headers=AUTH_HEADERS,
    )
    body = await r.get_json()
    assert body["region"] == "Berlin"
    assert body["count"] == 1


# ── Heuristic classifier ────────────────────────────────────────────────────

def test_heuristic_civic_patterns():
    cases = [
        "Standort der mobilen Wache in der Direktion 4",
        "Beteiligung der Öffentlichkeit zum Bebauungsplan XIV-263a",
        "Sprechstunde der Pflegestützpunkte Reinickendorf",
        "Stationäre Energieberatung der Verbraucherzentrale e.V.",
        "Berufsberatung für Frauen mit Migrationserfahrung",
    ]
    for title in cases:
        c = events_enrich.classify_heuristic(title)
        assert c.is_civic is True, f"expected civic: {title!r}"
        assert c.interest_score == 0
        assert c.category == "civic"


def test_heuristic_strong_cultural_patterns():
    cases = [
        ("Vernissage zur Ausstellung Foo", "art", 2),
        ("Konzert: Berliner Philharmoniker", "music", 2),
        ("Theater Geist - Das schönste Ei der Welt", "stage", 2),
        ("Karneval der Kulturen 2026", "festival", 3),
        ("Wochenmarkt Kollwitzplatz", "market", 2),
    ]
    for title, expected_cat, expected_score in cases:
        c = events_enrich.classify_heuristic(title)
        assert c.category == expected_cat, f"{title!r} → {c.category}"
        assert c.interest_score == expected_score
        assert c.is_civic is False


def test_heuristic_falls_back_to_other():
    c = events_enrich.classify_heuristic("Random title that matches nothing")
    assert c.category == "other"
    assert c.interest_score == 1
    assert c.is_civic is False


def test_apply_heuristic_writes_classification():
    events = [_ev(id="E1", title="Vernissage Foo"),
              _ev(id="E2", title="Sprechstunde Bürgeramt")]
    out = events_enrich.apply_heuristic(events)
    assert out[0].category == "art"
    assert out[0].is_civic is False
    assert out[1].category == "civic"
    assert out[1].is_civic is True
    # enriched_at stays None — that's the AI's column.
    assert out[0].enriched_at is None


# ── AI response parser ──────────────────────────────────────────────────────

def test_parse_response_jsonl_lines():
    text = (
        '{"id":"E1","category":"music","interest_score":3,"is_civic":false}\n'
        '{"id":"E2","category":"civic","interest_score":0,"is_civic":true}\n'
    )
    out = events_enrich._parse_response(text)
    assert set(out) == {"E1", "E2"}
    assert out["E1"].category == "music"
    assert out["E1"].interest_score == 3
    assert out["E2"].is_civic is True


def test_parse_response_tolerates_prose_and_fences():
    text = (
        "Sure, here you go:\n"
        "```json\n"
        '{"id":"E1","category":"art","interest_score":2,"is_civic":false}\n'
        "```\n"
    )
    out = events_enrich._parse_response(text)
    assert "E1" in out
    assert out["E1"].category == "art"


def test_parse_response_skips_invalid_categories():
    text = (
        '{"id":"E1","category":"music","interest_score":2,"is_civic":false}\n'
        '{"id":"E2","category":"banana","interest_score":2,"is_civic":false}\n'
    )
    out = events_enrich._parse_response(text)
    assert set(out) == {"E1"}


def test_parse_response_clamps_score():
    text = '{"id":"E1","category":"music","interest_score":99,"is_civic":false}\n'
    out = events_enrich._parse_response(text)
    assert out["E1"].interest_score == 3


# ── Schema preserves enrichment across re-imports ──────────────────────────

def test_replace_source_preserves_ai_classification(store):
    """AI-enriched rows (enriched_at set) survive a re-import."""
    e1 = _ev(id="E1", title="Vernissage", category="art",
             interest_score=2, is_civic=False, enriched_at=100.0)
    store.upsert_many([e1])

    re_imported = _ev(id="E1", title="Vernissage", category=None,
                      interest_score=None, is_civic=None, enriched_at=None)
    store.replace_source("Berlin", "test", [re_imported])

    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert rows[0].category == "art"
    assert rows[0].interest_score == 2
    assert rows[0].is_civic is False
    assert rows[0].enriched_at == 100.0


def test_replace_source_lets_heuristic_overwrite_unenriched(store):
    """If prior row was never AI-enriched (enriched_at NULL), a re-import
    with a fresh heuristic-classified event must overwrite it. Regression
    test for the bug where a NULL prior classification was carried forward
    and clobbered the new heuristic result."""
    prior = _ev(id="E1", title="Vernissage", category=None,
                interest_score=None, is_civic=None, enriched_at=None)
    store.upsert_many([prior])

    refreshed = _ev(id="E1", title="Vernissage", category="art",
                    interest_score=2, is_civic=False, enriched_at=None)
    store.replace_source("Berlin", "test", [refreshed])

    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert rows[0].category == "art"
    assert rows[0].interest_score == 2
    assert rows[0].is_civic is False


def test_query_unenriched(store):
    e1 = _ev(id="E1", enriched_at=None)
    e2 = _ev(id="E2", enriched_at=123.4)
    store.upsert_many([e1, e2])
    pending = store.query_unenriched()
    assert {p.id for p in pending} == {"E1"}


def test_update_classification(store):
    store.upsert_many([_ev(id="E1")])
    store.update_classification(
        "E1", category="music", interest_score=3,
        is_civic=False, enriched_at=42.0,
    )
    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert rows[0].category == "music"
    assert rows[0].interest_score == 3
    assert rows[0].enriched_at == 42.0


# ── Ticketmaster source ────────────────────────────────────────────────────

def _tm_event(**overrides):
    """Synthesise a TM Discovery API event payload."""
    base = {
        "id": "vvG1iZ4abc",
        "name": "Sample Concert",
        "url": "https://www.ticketmaster.de/event/abc",
        "dates": {
            "start": {
                "localDate": "2026-04-30",
                "localTime": "20:00:00",
                "noSpecificTime": False,
                "timeTBA": False,
            },
            "timezone": "Europe/Berlin",
        },
        "_embedded": {
            "venues": [{"name": "Berlin Arena", "city": {"name": "Berlin"}}]
        },
        "classifications": [{
            "primary": True,
            "segment": {"name": "Music"},
            "genre": {"name": "Rock"},
        }],
    }
    base.update(overrides)
    return base


def test_tm_normalise_music_event():
    ev = tm._normalise(_tm_event(), refreshed_at=10.0)
    assert ev is not None
    assert ev.id == "tm_vvG1iZ4abc"           # namespaced
    assert ev.title == "Sample Concert"
    assert ev.venue == "Berlin Arena"
    assert ev.category == "music"
    assert ev.interest_score == 2             # commercial floor
    assert ev.is_civic is False
    assert ev.is_free is False
    assert ev.source == "ticketmaster"
    assert ev.start_time == "20:00:00"


def test_tm_classify_uses_genre_for_arts_and_theatre():
    raw = _tm_event(classifications=[{
        "primary": True,
        "segment": {"name": "Arts & Theatre"},
        "genre": {"name": "Theatre"},
    }])
    ev = tm._normalise(raw, refreshed_at=0.0)
    assert ev.category == "stage"

    raw2 = _tm_event(classifications=[{
        "primary": True,
        "segment": {"name": "Arts & Theatre"},
        "genre": {"name": "Visual Arts"},
    }])
    ev2 = tm._normalise(raw2, refreshed_at=0.0)
    assert ev2.category == "art"


def test_tm_festival_score_3():
    raw = _tm_event(name="Lollapalooza Berlin Festival",
                    classifications=[{
                        "primary": True,
                        "segment": {"name": "Music"},
                        "genre": {"name": "Festivals"},
                    }])
    ev = tm._normalise(raw, refreshed_at=0.0)
    assert ev.category == "festival"
    assert ev.interest_score == 3


def test_tm_handles_missing_time():
    raw = _tm_event()
    raw["dates"]["start"]["noSpecificTime"] = True
    raw["dates"]["start"].pop("localTime", None)
    ev = tm._normalise(raw, refreshed_at=0.0)
    assert ev.start_time is None


def test_tm_returns_none_on_missing_required():
    # No name
    assert tm._normalise(_tm_event(name=""), refreshed_at=0.0) is None
    # No date
    raw = _tm_event()
    raw["dates"]["start"].pop("localDate")
    assert tm._normalise(raw, refreshed_at=0.0) is None


async def test_tm_fetch_disabled_without_key(monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    out = await tm.fetch_events()
    assert out == []


def _tm_raw_with(name, attr_id="K_ROSALIA", venue_id="V_UBER", date="2026-05-01"):
    """Raw TM payload tailored for dedup tests."""
    return {
        "id": f"tm_{name.replace(' ', '_')}",
        "name": name,
        "dates": {"start": {"localDate": date}},
        "_embedded": {
            "venues": [{"id": venue_id, "name": "Uber Arena"}],
            "attractions": [{"id": attr_id, "name": "ROSALÍA"}],
        },
        "classifications": [{"primary": True,
                             "segment": {"name": "Music"},
                             "genre": {"name": "Pop"}}],
    }


def test_tm_dedup_collapses_ticket_tiers():
    """The classic ROSALÍA case: base + VIP + Logen-Seat all share
    (attraction, date, venue) and must collapse to the base."""
    raws = [
        _tm_raw_with("ROSALÍA: LUX TOUR 2026 | VIP Packages"),
        _tm_raw_with("ROSALÍA: LUX TOUR 2026"),
        _tm_raw_with("ROSALÍA: LUX TOUR 2026 | Logen-Seat in der Ticketmaster Suite"),
    ]
    deduped = tm._dedup(raws)
    assert len(deduped) == 1
    assert deduped[0]["name"] == "ROSALÍA: LUX TOUR 2026"   # base ticket wins


def test_tm_dedup_keeps_distinct_dates():
    """Same artist + venue on different nights are different shows."""
    raws = [
        _tm_raw_with("Peaches Tour 2026", date="2026-05-01"),
        _tm_raw_with("Peaches Tour 2026", date="2026-05-02"),
    ]
    assert len(tm._dedup(raws)) == 2


def test_tm_dedup_keeps_distinct_venues():
    """Same artist on the same date at different venues are different shows."""
    raws = [
        _tm_raw_with("ROSALÍA: LUX TOUR 2026", venue_id="V_UBER"),
        _tm_raw_with("ROSALÍA: LUX TOUR 2026", venue_id="V_VELODROM"),
    ]
    assert len(tm._dedup(raws)) == 2


def test_tm_dedup_passes_through_when_attractions_missing():
    """Some events (rare) have no attractions array — those can't be
    grouped, so they pass through as-is rather than collapsing wrongly."""
    raw_no_attr = {
        "id": "tm_xyz",
        "name": "Some Event",
        "dates": {"start": {"localDate": "2026-05-01"}},
        "_embedded": {"venues": [{"id": "V"}]},
    }
    out = tm._dedup([raw_no_attr, raw_no_attr])
    assert len(out) == 2  # both pass through


# ── Yorck cinema source ────────────────────────────────────────────────────

def _yorck_session(start_time, cinema_name):
    return {
        "sys": {"id": f"sess-{start_time}-{cinema_name}"},
        "fields": {
            "startTime": start_time,
            "cinema": {"fields": {"name": cinema_name}},
        },
    }


def _yorck_film(title, slug, sessions):
    return {
        "sys": {"id": f"film-{slug}"},
        "fields": {"title": title, "slug": slug, "sessions": sessions},
    }


def _yorck_data(films, promoted_slugs=()):
    return {
        "props": {
            "pageProps": {
                "films": films,
                "top5PromotedSpecials": [
                    {"slug": s} for s in promoted_slugs
                ],
            }
        }
    }


def test_yorck_collapses_same_day_same_cinema_sessions_to_one_event():
    """Multiple showtimes of the same film on the same day at the same
    cinema collapse to ONE event carrying the earliest start_time. Goal:
    cards represent films-per-cinema-per-day so the UI can dedup across
    times on the client and rank cinemas by distance."""
    today = date.today()
    today_iso = today.isoformat()
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today_iso}T17:30:00+02:00", "delphi LUX"),
        _yorck_session(f"{today_iso}T19:30:00+02:00", "delphi LUX"),
        _yorck_session(f"{today_iso}T21:30:00+02:00", "delphi LUX"),
    ])])
    events = yorck._events_in_window(data, start=today, days=14, refreshed_at=0.0)
    assert len(events) == 1
    e = events[0]
    assert e.title == "Rose"
    assert e.venue == "delphi LUX"
    assert e.start_time == "17:30:00"
    assert e.category == "film"
    assert e.is_civic is False
    assert e.source == "yorck"
    assert e.region == "Berlin"


def test_yorck_groups_per_day_separately():
    """Same film on two different days at the same cinema → two events."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today.isoformat()}T19:30:00+02:00", "delphi LUX"),
        _yorck_session(f"{tomorrow.isoformat()}T19:30:00+02:00", "delphi LUX"),
    ])])
    events = yorck._events_in_window(data, start=today, days=14, refreshed_at=0.0)
    assert len(events) == 2
    assert {e.start_date for e in events} == {today.isoformat(), tomorrow.isoformat()}
    assert {e.venue for e in events} == {"delphi LUX"}


def test_yorck_emits_one_event_per_cinema_on_same_day():
    """A film showing at three cinemas on the same day produces three
    events — one per cinema — so the frontend can list cinemas per movie
    and rank them by distance."""
    today = date.today().isoformat()
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today}T19:00:00+02:00", "delphi LUX"),
        _yorck_session(f"{today}T20:00:00+02:00", "Kant Kino"),
        _yorck_session(f"{today}T21:00:00+02:00", "Rollberg"),
    ])])
    events = yorck._events_in_window(data, start=date.today(), days=14, refreshed_at=0.0)
    assert len(events) == 3
    venues = {e.venue for e in events}
    assert venues == {"delphi LUX", "Kant Kino", "Rollberg"}
    # Each event has its own ID derived from cinema slug.
    assert len({e.id for e in events}) == 3


def test_yorck_attaches_cinema_directory_metadata():
    """When a cinema directory is supplied, each event carries the
    cinema's slug-derived URL plus its lat/lon so the UI can sort by
    distance and link out to the right program page."""
    today = date.today().isoformat()
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today}T19:00:00+02:00", "delphi LUX"),
    ])])
    directory = {
        "delphi LUX": {
            "slug": "delphi-lux",
            "lat": 52.50557,
            "lon": 13.32961,
            "url": "https://www.yorck.de/kinos/delphi-lux",
        },
    }
    events = yorck._events_in_window(
        data, start=date.today(), days=14, refreshed_at=0.0,
        cinema_directory=directory,
    )
    assert len(events) == 1
    e = events[0]
    assert e.venue_url == "https://www.yorck.de/kinos/delphi-lux"
    assert e.venue_lat == 52.50557
    assert e.venue_lon == 13.32961


def test_yorck_propagates_image_and_actors():
    """Hero image from the listings payload + cast from the detail-page
    metadata flow through to every (film, cinema, day) event so the
    movie card has a poster and an actors line without extra requests."""
    today = date.today().isoformat()
    film = _yorck_film("Rose", "rose", [
        _yorck_session(f"{today}T19:00:00+02:00", "delphi LUX"),
        _yorck_session(f"{today}T20:00:00+02:00", "Kant Kino"),
    ])
    film["fields"]["heroImage"] = {"fields": {"image": {"fields": {
        "file": {"url": "//images.ctfassets.net/x/y/z/Rose"},
    }}}}
    data = _yorck_data([film])
    events = yorck._events_in_window(
        data, start=date.today(), days=14, refreshed_at=0.0,
        film_metadata={"rose": {"cast": "Sandra Hüller, Caro Braun"}},
    )
    assert len(events) == 2
    for e in events:
        assert e.image_url == "https://images.ctfassets.net/x/y/z/Rose?w=480&fm=webp&q=80"
        assert e.actors == "Sandra Hüller, Caro Braun"


def test_yorck_no_metadata_leaves_actors_none():
    today = date.today().isoformat()
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today}T19:00:00+02:00", "delphi LUX"),
    ])])
    events = yorck._events_in_window(
        data, start=date.today(), days=14, refreshed_at=0.0,
    )
    assert events[0].actors is None
    # No heroImage in the test fixture either.
    assert events[0].image_url is None


def test_yorck_unknown_cinema_falls_back_to_slugified_url():
    """Without a directory entry, the URL is built from a slugified name
    so the cinema link still works (Yorck's URL scheme is /kinos/<slug>)."""
    today = date.today().isoformat()
    data = _yorck_data([_yorck_film("Rose", "rose", [
        _yorck_session(f"{today}T19:00:00+02:00", "Kant Kino"),
    ])])
    events = yorck._events_in_window(
        data, start=date.today(), days=14, refreshed_at=0.0,
    )
    assert len(events) == 1
    assert events[0].venue_url == "https://www.yorck.de/kinos/kant-kino"
    assert events[0].venue_lat is None
    assert events[0].venue_lon is None


def test_yorck_window_filtering():
    """Sessions outside the days window are excluded."""
    today = date.today()
    way_later = (today + timedelta(days=60)).isoformat()
    data = _yorck_data([_yorck_film("Future Film", "fut", [
        _yorck_session(f"{way_later}T19:00:00+02:00", "delphi LUX"),
    ])])
    events = yorck._events_in_window(data, start=today, days=14, refreshed_at=0.0)
    assert events == []


def test_yorck_promoted_film_gets_score_3():
    today = date.today().isoformat()
    data = _yorck_data(
        [_yorck_film("Hot Film", "hot", [
            _yorck_session(f"{today}T19:00:00+02:00", "delphi LUX"),
        ])],
        promoted_slugs=["hot"],
    )
    events = yorck._events_in_window(data, start=date.today(), days=14, refreshed_at=0.0)
    assert events[0].interest_score == 3


def test_yorck_skips_film_without_sessions():
    """Films with no sessions in the window contribute nothing."""
    data = _yorck_data([_yorck_film("Empty", "empty", [])])
    events = yorck._events_in_window(data, start=date.today(), days=14, refreshed_at=0.0)
    assert events == []


def test_yorck_parse_next_data_handles_missing_blob():
    assert yorck._parse_next_data("<html><body>no script here</body></html>") is None


# ── Kinoheld GraphQL source ───────────────────────────────────────────────

from src.events import source_kinoheld as kh  # noqa: E402


def _kh_show(beginning, *, movie_id="m1", title="Demo", country=None,
             actors=None, image=None):
    show = {"beginning": beginning, "movie": {
        "id": movie_id, "title": title,
        "productionCountries": [{"name": country}] if country else [],
        "actors": [{"name": a} for a in (actors or [])],
        "thumbnailImage": {"url": image} if image else None,
    }}
    return show


def _kh_cinema(id_="c1", name="Demo Cinema", slug="demo-cinema",
               city_slug="berlin", lat=52.5, lon=13.4):
    return {
        "id": id_, "name": name, "urlSlug": slug,
        "latitude": lat, "longitude": lon,
        "city": {"name": "Berlin", "urlSlug": city_slug},
    }


def test_kh_normalise_show_emits_full_event():
    """A complete show payload with country/actors/image yields one event
    populated end-to-end. Goal: anything Kinoheld gives us survives the
    mapping intact, including the cinema URL we synthesize."""
    show = _kh_show(
        "2026-05-09T19:30:00+02:00",
        movie_id="42", title="Rose", country="Deutschland",
        actors=["Sandra Hüller", "Caro Braun"],
        image="https://static.kinoheld.de/images/film/rose.jpg",
    )
    cinema = _kh_cinema(id_="100", name="Delphi LUX", slug="delphi-lux")
    ev = kh._normalise_show(show, cinema, region="Berlin", refreshed_at=0.0)
    assert ev is not None
    assert ev.id == "kh_42_100_2026-05-09"
    assert ev.title == "Rose"
    assert ev.venue == "Delphi LUX"
    assert ev.region == "Berlin"
    assert ev.start_date == "2026-05-09"
    assert ev.start_time == "19:30:00"
    assert ev.country == "Deutschland"
    assert ev.actors == "Sandra Hüller, Caro Braun"
    assert ev.image_url == "https://static.kinoheld.de/images/film/rose.jpg"
    assert ev.venue_url == "https://www.kinoheld.de/kino/berlin/delphi-lux"
    assert ev.venue_lat == 52.5
    assert ev.venue_lon == 13.4
    assert ev.category == "film"
    assert ev.source == "kinoheld"


def test_kh_normalise_show_drops_when_required_fields_missing():
    """A show with no movie title, no movie id, or no beginning is
    unusable — caller should drop it rather than emit a half-built row."""
    cinema = _kh_cinema()
    no_title = _kh_show("2026-05-09T19:30:00+02:00", title="")
    no_id = _kh_show("2026-05-09T19:30:00+02:00", movie_id="")
    no_time = _kh_show("", title="Real Title")
    for show in (no_title, no_id, no_time):
        assert kh._normalise_show(show, cinema, region="Berlin", refreshed_at=0.0) is None


def test_kh_collapse_keeps_earliest_showtime():
    """Two showtimes of the same movie at the same cinema on the same
    day must collapse to ONE event keeping the earliest start_time, so
    the card represents a film-day, not individual showings."""
    cinema = _kh_cinema(id_="100")
    showings = [
        _kh_show("2026-05-09T21:00:00+02:00", movie_id="42"),
        _kh_show("2026-05-09T17:30:00+02:00", movie_id="42"),
        _kh_show("2026-05-09T19:30:00+02:00", movie_id="42"),
    ]
    events = [
        kh._normalise_show(s, cinema, region="Berlin", refreshed_at=0.0)
        for s in showings
    ]
    collapsed = kh._collapse_per_movie_cinema_day(events)
    assert len(collapsed) == 1
    assert collapsed[0].start_time == "17:30:00"


def test_kh_format_country_caps_at_three():
    """German co-productions list a long tail of countries — show at
    most three so the meta line on the card stays readable."""
    countries = [
        {"name": "Deutschland"}, {"name": "Frankreich"},
        {"name": "USA"}, {"name": "Libanon"}, {"name": "Katar"},
    ]
    assert kh._format_country(countries) == "Deutschland, Frankreich, USA"
    assert kh._format_country([]) is None
    assert kh._format_country([{"name": ""}]) is None


def test_kh_normalise_handles_missing_city_slug():
    """If the cinema is missing city.urlSlug we can't build a venue URL —
    leave it None rather than producing a broken /kino// link."""
    show = _kh_show("2026-05-09T19:00:00+02:00")
    cinema = {
        "id": "100", "name": "Demo", "urlSlug": "demo",
        "latitude": 52.5, "longitude": 13.4,
        "city": {"name": "Berlin"},  # no urlSlug
    }
    ev = kh._normalise_show(show, cinema, region="Berlin", refreshed_at=0.0)
    assert ev is not None
    assert ev.venue_url is None
