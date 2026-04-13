"""Weather API routes using Open-Meteo (forecasts) and Bright Sky / DWD (current observations)."""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from quart import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
weather_bp = Blueprint("weather", __name__)

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
BRIGHTSKY_CURRENT = "https://api.brightsky.dev/current_weather"

# WMO weather code -> (description, icon name)
WMO_CODES = {
    0: ("Klar", "clear"),
    1: ("Überwiegend klar", "mostly-clear"),
    2: ("Teilweise bewölkt", "partly-cloudy"),
    3: ("Bewölkt", "overcast"),
    45: ("Nebel", "fog"),
    48: ("Nebel mit Reif", "fog"),
    51: ("Leichter Nieselregen", "drizzle-light"),
    53: ("Nieselregen", "drizzle"),
    55: ("Starker Nieselregen", "drizzle-heavy"),
    56: ("Gefrierender Nieselregen", "freezing-drizzle"),
    57: ("Starker gefr. Nieselregen", "freezing-drizzle"),
    61: ("Leichter Regen", "rain-light"),
    63: ("Regen", "rain"),
    65: ("Starker Regen", "rain-heavy"),
    66: ("Gefrierender Regen", "freezing-rain"),
    67: ("Starker gefr. Regen", "freezing-rain"),
    71: ("Leichter Schneefall", "snow-light"),
    73: ("Schneefall", "snow"),
    75: ("Starker Schneefall", "snow-heavy"),
    77: ("Schneegriesel", "snow-grains"),
    80: ("Leichte Regenschauer", "showers-light"),
    81: ("Regenschauer", "showers"),
    82: ("Starke Regenschauer", "showers-heavy"),
    85: ("Leichte Schneeschauer", "snow-showers"),
    86: ("Starke Schneeschauer", "snow-showers-heavy"),
    95: ("Gewitter", "thunderstorm"),
    96: ("Gewitter mit Hagel", "thunderstorm-hail"),
    99: ("Starkes Gewitter mit Hagel", "thunderstorm-hail"),
}


def _weather_code_info(code: int | None) -> dict:
    if code is None:
        return {"description": "Unbekannt", "icon": "unknown"}
    desc, icon = WMO_CODES.get(code, ("Unbekannt", "unknown"))
    return {"description": desc, "icon": icon}


# ── Bright Sky (DWD) icon -> our (description, icon) ────
BRIGHTSKY_ICON_MAP = {
    "clear-day": ("Klar", "clear"),
    "clear-night": ("Klar", "clear"),
    "partly-cloudy-day": ("Teilweise bewölkt", "partly-cloudy"),
    "partly-cloudy-night": ("Teilweise bewölkt", "partly-cloudy"),
    "cloudy": ("Bewölkt", "overcast"),
    "fog": ("Nebel", "fog"),
    "wind": ("Windig", "clear"),
    "rain": ("Regen", "rain"),
    "sleet": ("Schneeregen", "freezing-rain"),
    "snow": ("Schneefall", "snow"),
    "hail": ("Gewitter mit Hagel", "thunderstorm-hail"),
    "thunderstorm": ("Gewitter", "thunderstorm"),
}


async def _fetch_openmeteo(session, params):
    """Fetch forecast data from Open-Meteo."""
    try:
        async with session.get(OPEN_METEO_FORECAST, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Open-Meteo error %s: %s", resp.status, text)
                return None
            return await resp.json()
    except Exception as e:
        logger.error("Open-Meteo fetch failed: %s", e)
        return None


async def _fetch_brightsky(session, lat, lon):
    """Fetch current observations from Bright Sky (DWD stations)."""
    try:
        params = {"lat": lat, "lon": lon, "tz": "Europe/Berlin", "units": "dwd"}
        async with session.get(BRIGHTSKY_CURRENT, params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("Bright Sky fetch failed: %s", e)
        return None


def _parse_brightsky_current(bs_data, is_day_fallback=None):
    """Parse Bright Sky current_weather response into our format.

    Returns a dict matching our ``current`` response shape, or None on failure.
    """
    if not bs_data or "weather" not in bs_data:
        return None

    w = bs_data["weather"]

    # Require at least a valid temperature
    if w.get("temperature") is None:
        return None

    bs_icon = w.get("icon") or ""
    mapped = BRIGHTSKY_ICON_MAP.get(bs_icon)
    if not mapped:
        return None
    desc, our_icon = mapped

    # is_day from icon suffix, fall back to Open-Meteo value
    if bs_icon.endswith("-day"):
        is_day = 1
    elif bs_icon.endswith("-night"):
        is_day = 0
    else:
        is_day = is_day_fallback

    # Refine 'wind' icon using cloud_cover
    cloud = w.get("cloud_cover")
    if bs_icon == "wind" and cloud is not None:
        if cloud < 25:
            desc, our_icon = ("Klar", "clear")
        elif cloud < 75:
            desc, our_icon = ("Teilweise bewölkt", "partly-cloudy")
        else:
            desc, our_icon = ("Bewölkt", "overcast")

    # Refine rain/snow intensity from 10-min precipitation
    precip_10 = w.get("precipitation_10") or 0
    if bs_icon == "rain":
        if precip_10 < 0.2:
            desc, our_icon = ("Leichter Regen", "rain-light")
        elif precip_10 >= 2.0:
            desc, our_icon = ("Starker Regen", "rain-heavy")
    elif bs_icon == "snow":
        if precip_10 < 0.3:
            desc, our_icon = ("Leichter Schneefall", "snow-light")
        elif precip_10 >= 2.0:
            desc, our_icon = ("Starker Schneefall", "snow-heavy")

    # Build source station info
    source = None
    sources = bs_data.get("sources", [])
    if sources:
        src = sources[0]
        name = src.get("station_name", "")
        dist_m = src.get("distance", 0)
        dist_km = round(dist_m / 1000, 1)
        source = f"{name} ({dist_km} km)"

    return {
        "temp": w.get("temperature"),
        "wind": w.get("wind_speed_10"),
        "wind_dir": w.get("wind_direction_10"),
        "humidity": w.get("relative_humidity"),
        "cloud": cloud,
        "code": None,
        "icon": our_icon,
        "desc": desc,
        "is_day": is_day,
        "time": w.get("timestamp"),
        "source": source,
    }


@weather_bp.route("/api/geocode")
async def geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    params = {"name": q, "count": 8, "language": "de", "format": "json"}
    async with aiohttp.ClientSession() as s:
        async with s.get(OPEN_METEO_GEOCODE, params=params) as resp:
            data = await resp.json()
    results = []
    for r in data.get("results", []):
        results.append({
            "name": r.get("name", ""),
            "admin1": r.get("admin1", ""),
            "country": r.get("country", ""),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
        })
    return jsonify(results)


@weather_bp.route("/api/weather")
async def weather():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lon required"}), 400

    om_params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "apparent_temperature",
            "precipitation_probability",
            "precipitation",
            "weathercode",
            "windspeed_10m",
            "winddirection_10m",
            "relativehumidity_2m",
            "cloudcover",
            "is_day",
        ]),
        "daily": ",".join([
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "windspeed_10m_max",
            "winddirection_10m_dominant",
            "sunrise",
            "sunset",
            "uv_index_max",
            "sunshine_duration",
        ]),
        "current_weather": "true",
        "timezone": "Europe/Berlin",
        "forecast_days": 7,
        "models": "icon_seamless",
    }

    # Fetch Open-Meteo forecasts and Bright Sky observations in parallel
    async with aiohttp.ClientSession() as s:
        om_data, bs_data = await asyncio.gather(
            _fetch_openmeteo(s, om_params),
            _fetch_brightsky(s, lat, lon),
        )

    if om_data is None:
        return jsonify({"error": "Weather API error"}), 502

    # ── Current weather: prefer real DWD observations ────
    om_current = om_data.get("current_weather", {})
    bs_current = _parse_brightsky_current(
        bs_data, is_day_fallback=om_current.get("is_day"),
    )

    if bs_current:
        current_out = bs_current
    else:
        # Fallback to Open-Meteo model data
        current_code = om_current.get("weathercode")
        current_info = _weather_code_info(current_code)
        current_out = {
            "temp": om_current.get("temperature"),
            "wind": om_current.get("windspeed"),
            "wind_dir": om_current.get("winddirection"),
            "humidity": None,
            "cloud": None,
            "code": current_code,
            "icon": current_info["icon"],
            "desc": current_info["description"],
            "is_day": om_current.get("is_day"),
            "time": om_current.get("time"),
            "source": None,
        }

    # ── Hourly forecast ─────────────────────────────────────
    hourly_raw = om_data.get("hourly", {})
    times = hourly_raw.get("time", [])
    hourly = []
    for i, t in enumerate(times):
        code = hourly_raw.get("weathercode", [None] * len(times))[i]
        info = _weather_code_info(code)
        hourly.append({
            "time": t,
            "temp": hourly_raw.get("temperature_2m", [])[i],
            "apparent_temp": hourly_raw.get("apparent_temperature", [])[i],
            "precip_prob": hourly_raw.get("precipitation_probability", [])[i],
            "precip": hourly_raw.get("precipitation", [])[i],
            "wind": hourly_raw.get("windspeed_10m", [])[i],
            "wind_dir": hourly_raw.get("winddirection_10m", [])[i],
            "humidity": hourly_raw.get("relativehumidity_2m", [])[i],
            "cloud": hourly_raw.get("cloudcover", [])[i],
            "is_day": hourly_raw.get("is_day", [])[i],
            "code": code,
            "icon": info["icon"],
            "desc": info["description"],
        })

    # ── Daily forecast ──────────────────────────────────────
    daily_raw = om_data.get("daily", {})
    daily_times = daily_raw.get("time", [])
    daily = []
    for i, t in enumerate(daily_times):
        code = daily_raw.get("weathercode", [None] * len(daily_times))[i]
        info = _weather_code_info(code)
        daily.append({
            "date": t,
            "temp_max": daily_raw.get("temperature_2m_max", [])[i],
            "temp_min": daily_raw.get("temperature_2m_min", [])[i],
            "precip_sum": daily_raw.get("precipitation_sum", [])[i],
            "precip_prob": daily_raw.get("precipitation_probability_max", [])[i],
            "wind_max": daily_raw.get("windspeed_10m_max", [])[i],
            "wind_dir": daily_raw.get("winddirection_10m_dominant", [])[i],
            "sunrise": daily_raw.get("sunrise", [])[i],
            "sunset": daily_raw.get("sunset", [])[i],
            "uv_max": daily_raw.get("uv_index_max", [])[i],
            "sun_hours": round((daily_raw.get("sunshine_duration", [0] * len(daily_times))[i] or 0) / 3600, 1),
            "code": code,
            "icon": info["icon"],
            "desc": info["description"],
        })

    return jsonify({
        "current": current_out,
        "hourly": hourly,
        "daily": daily,
    })
