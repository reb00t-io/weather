"""Tests for the events module: store, source normalisation, and API."""

from __future__ import annotations

import os
from datetime import date

import pytest

os.environ.setdefault("API_KEY", "test-api-key")

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
