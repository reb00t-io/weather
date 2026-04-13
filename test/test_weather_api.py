"""Tests for the weather API endpoints."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set required env vars before importing app
os.environ.setdefault("API_KEY", "test-api-key")

from src.main import API_KEY, app  # noqa: E402
from src.weather_api import WMO_CODES, _weather_code_info  # noqa: E402

AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    return app.test_client()


def _mock_aiohttp_get(json_data, status=200):
    """Create a mock for aiohttp.ClientSession().get() context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=json.dumps(json_data))

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=ctx)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    return patch("src.weather_api.aiohttp.ClientSession", return_value=session)


SAMPLE_GEOCODE_RESPONSE = {
    "results": [
        {
            "name": "Berlin",
            "admin1": "Berlin",
            "country": "Deutschland",
            "latitude": 52.52437,
            "longitude": 13.41053,
        },
        {
            "name": "Berlin",
            "admin1": "New Hampshire",
            "country": "USA",
            "latitude": 44.46867,
            "longitude": -71.18508,
        },
    ]
}

SAMPLE_WEATHER_RESPONSE = {
    "current_weather": {
        "temperature": 12.5,
        "windspeed": 15.3,
        "winddirection": 225,
        "weathercode": 3,
        "is_day": 1,
        "time": "2026-04-12T14:00",
    },
    "hourly": {
        "time": [f"2026-04-12T{h:02d}:00" for h in range(24)],
        "temperature_2m": [8 + i * 0.5 for i in range(24)],
        "apparent_temperature": [6 + i * 0.5 for i in range(24)],
        "precipitation_probability": [10 + i for i in range(24)],
        "precipitation": [0.0] * 24,
        "weathercode": [3] * 24,
        "windspeed_10m": [15.0] * 24,
        "winddirection_10m": [225] * 24,
        "relativehumidity_2m": [65] * 24,
        "cloudcover": [80] * 24,
        "is_day": [0]*6 + [1]*12 + [0]*6,
    },
    "daily": {
        "time": [f"2026-04-{12+i}" for i in range(7)],
        "weathercode": [3, 61, 0, 2, 63, 1, 0],
        "temperature_2m_max": [14, 12, 18, 16, 11, 15, 20],
        "temperature_2m_min": [6, 5, 7, 8, 4, 6, 9],
        "apparent_temperature_max": [12, 10, 16, 14, 9, 13, 18],
        "apparent_temperature_min": [3, 2, 4, 5, 1, 3, 6],
        "precipitation_sum": [0.0, 5.2, 0.0, 0.0, 8.1, 0.0, 0.0],
        "precipitation_probability_max": [10, 75, 5, 20, 85, 15, 0],
        "windspeed_10m_max": [20, 25, 12, 18, 30, 15, 10],
        "winddirection_10m_dominant": [225, 180, 90, 270, 200, 315, 45],
        "sunrise": [f"2026-04-{12+i}T06:{20+i:02d}" for i in range(7)],
        "sunset": [f"2026-04-{12+i}T20:{10+i:02d}" for i in range(7)],
        "uv_index_max": [3, 2, 6, 5, 1, 4, 7],
        "sunshine_duration": [10800, 3600, 28800, 21600, 0, 18000, 36000],
    },
}


# ── Index ───────────────────────────────────────────────────────────────────

async def test_index_returns_200(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = await resp.get_data(as_text=True)
    assert "Wetter" in data


# ── Auth ────────────────────────────────────────────────────────────────────

async def test_api_without_auth_returns_401(client):
    resp = await client.get("/api/geocode?q=Berlin")
    assert resp.status_code == 401


async def test_api_with_wrong_key_returns_401(client):
    resp = await client.get("/api/geocode?q=Berlin", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


# ── Geocode ─────────────────────────────────────────────────────────────────

async def test_geocode_empty_query_returns_empty(client):
    resp = await client.get("/api/geocode?q=", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data == []


async def test_geocode_returns_results(client):
    with _mock_aiohttp_get(SAMPLE_GEOCODE_RESPONSE):
        resp = await client.get("/api/geocode?q=Berlin", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = await resp.get_json()
    assert len(data) == 2
    assert data[0]["name"] == "Berlin"
    assert data[0]["lat"] == 52.52437
    assert data[0]["lon"] == 13.41053
    assert data[0]["country"] == "Deutschland"


async def test_geocode_no_results(client):
    with _mock_aiohttp_get({"results": []}):
        resp = await client.get("/api/geocode?q=xyznonexistent", headers=AUTH_HEADERS)
    data = await resp.get_json()
    assert data == []


async def test_geocode_missing_results_key(client):
    with _mock_aiohttp_get({}):
        resp = await client.get("/api/geocode?q=test", headers=AUTH_HEADERS)
    data = await resp.get_json()
    assert data == []


# ── Weather ─────────────────────────────────────────────────────────────────

async def test_weather_missing_params_returns_400(client):
    resp = await client.get("/api/weather", headers=AUTH_HEADERS)
    assert resp.status_code == 400


async def test_weather_missing_lon_returns_400(client):
    resp = await client.get("/api/weather?lat=52.52", headers=AUTH_HEADERS)
    assert resp.status_code == 400


async def test_weather_invalid_lat_returns_400(client):
    resp = await client.get("/api/weather?lat=abc&lon=13.41", headers=AUTH_HEADERS)
    assert resp.status_code == 400


async def test_weather_returns_forecast(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = await resp.get_json()

    # Current weather
    assert data["current"]["temp"] == 12.5
    assert data["current"]["wind"] == 15.3
    assert data["current"]["desc"] == "Bew\u00f6lkt"
    assert data["current"]["icon"] == "overcast"

    # Hourly
    assert len(data["hourly"]) == 24
    assert data["hourly"][0]["time"] == "2026-04-12T00:00"
    assert "temp" in data["hourly"][0]
    assert "precip_prob" in data["hourly"][0]
    assert "icon" in data["hourly"][0]

    # Daily
    assert len(data["daily"]) == 7
    assert data["daily"][0]["date"] == "2026-04-12"
    assert data["daily"][0]["temp_max"] == 14
    assert data["daily"][0]["temp_min"] == 6
    assert data["daily"][0]["precip_prob"] == 10


async def test_weather_daily_structure(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    data = await resp.get_json()

    day = data["daily"][1]
    assert day["desc"] == "Leichter Regen"
    assert day["icon"] == "rain-light"
    assert day["precip_sum"] == 5.2
    assert day["wind_max"] == 25
    assert day["uv_max"] == 2
    assert "sunrise" in day
    assert "sunset" in day


async def test_weather_api_error_returns_502(client):
    with _mock_aiohttp_get({}, status=500):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    assert resp.status_code == 502


# ── Weather code mapping ────────────────────────────────────────────────────

def test_weather_code_info_known():
    info = _weather_code_info(0)
    assert info["description"] == "Klar"
    assert info["icon"] == "clear"


def test_weather_code_info_rain():
    info = _weather_code_info(63)
    assert info["description"] == "Regen"
    assert info["icon"] == "rain"


def test_weather_code_info_thunderstorm():
    info = _weather_code_info(95)
    assert info["description"] == "Gewitter"
    assert info["icon"] == "thunderstorm"


def test_weather_code_info_unknown():
    info = _weather_code_info(999)
    assert info["description"] == "Unbekannt"
    assert info["icon"] == "unknown"


def test_weather_code_info_none():
    info = _weather_code_info(None)
    assert info["icon"] == "unknown"


def test_all_wmo_codes_have_valid_entries():
    for code in WMO_CODES:
        info = _weather_code_info(code)
        assert info["description"]
        assert info["icon"]
        assert info["icon"] != "unknown"


# ── Weather data update consistency ─────────────────────────────────────────

async def test_weather_hourly_contains_all_required_fields(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    data = await resp.get_json()

    required_fields = {"time", "temp", "apparent_temp", "precip_prob", "precip",
                       "wind", "wind_dir", "humidity", "cloud", "is_day",
                       "code", "icon", "desc"}
    for h in data["hourly"]:
        assert required_fields.issubset(h.keys()), f"Missing fields: {required_fields - set(h.keys())}"


async def test_weather_daily_contains_all_required_fields(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    data = await resp.get_json()

    required_fields = {"date", "temp_max", "temp_min", "precip_sum", "precip_prob",
                       "wind_max", "wind_dir", "sunrise", "sunset", "uv_max", "sun_hours",
                       "feels_max", "feels_min",
                       "code", "icon", "desc"}
    for d in data["daily"]:
        assert required_fields.issubset(d.keys()), f"Missing fields: {required_fields - set(d.keys())}"


async def test_weather_current_contains_all_required_fields(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    data = await resp.get_json()

    required_fields = {"temp", "wind", "wind_dir", "code", "icon", "desc", "is_day", "time"}
    assert required_fields.issubset(data["current"].keys())


async def test_weather_response_contains_aqi_and_warnings(client):
    with _mock_aiohttp_get(SAMPLE_WEATHER_RESPONSE):
        resp = await client.get("/api/weather?lat=52.52&lon=13.41", headers=AUTH_HEADERS)
    data = await resp.get_json()
    # aqi and warnings should be present (may be null/empty if upstream fails)
    assert "aqi" in data
    assert "warnings" in data
    assert isinstance(data["warnings"], list)
