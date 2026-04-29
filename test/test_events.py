"""Tests for the events module: store, source normalisation, and API."""

from __future__ import annotations

import os
from datetime import date

import pytest

os.environ.setdefault("API_KEY", "test-api-key")

from src.events import enrich as events_enrich  # noqa: E402
from src.events import service as events_service  # noqa: E402
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


def test_store_replace_region_atomic(store):
    store.upsert_many([
        _ev(id="OLD1"),
        _ev(id="OLD2"),
        _ev(id="KEEP", region="Munich"),
    ])
    store.replace_region("Berlin", [_ev(id="NEW1")])
    berlin = store.query("Berlin", "2026-04-29", "2026-05-13")
    munich = store.query("Munich", "2026-04-29", "2026-05-13")
    assert [r.id for r in berlin] == ["NEW1"]
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

def test_replace_region_preserves_ai_classification(store):
    """AI-enriched rows (enriched_at set) survive a re-import."""
    e1 = _ev(id="E1", title="Vernissage", category="art",
             interest_score=2, is_civic=False, enriched_at=100.0)
    store.upsert_many([e1])

    re_imported = _ev(id="E1", title="Vernissage", category=None,
                      interest_score=None, is_civic=None, enriched_at=None)
    store.replace_region("Berlin", [re_imported])

    rows = store.query("Berlin", "2026-04-29", "2026-05-13")
    assert rows[0].category == "art"
    assert rows[0].interest_score == 2
    assert rows[0].is_civic is False
    assert rows[0].enriched_at == 100.0


def test_replace_region_lets_heuristic_overwrite_unenriched(store):
    """If prior row was never AI-enriched (enriched_at NULL), a re-import
    with a fresh heuristic-classified event must overwrite it. Regression
    test for the bug where a NULL prior classification was carried forward
    and clobbered the new heuristic result."""
    # Existing row: heuristic-only or stale; enriched_at is NULL.
    prior = _ev(id="E1", title="Vernissage", category=None,
                interest_score=None, is_civic=None, enriched_at=None)
    store.upsert_many([prior])

    # Refresh: new event with heuristic classification applied.
    refreshed = _ev(id="E1", title="Vernissage", category="art",
                    interest_score=2, is_civic=False, enriched_at=None)
    store.replace_region("Berlin", [refreshed])

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
