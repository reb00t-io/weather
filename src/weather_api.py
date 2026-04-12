"""Weather API routes using Open-Meteo as data source."""

import logging
from datetime import datetime, timezone

import aiohttp
from quart import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
weather_bp = Blueprint("weather", __name__)

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

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

    params = {
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
        ]),
        "current_weather": "true",
        "timezone": "Europe/Berlin",
        "forecast_days": 7,
        "models": "icon_seamless",
    }

    async with aiohttp.ClientSession() as s:
        async with s.get(OPEN_METEO_FORECAST, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Open-Meteo error %s: %s", resp.status, text)
                return jsonify({"error": "Weather API error"}), 502
            data = await resp.json()

    current = data.get("current_weather", {})
    current_code = current.get("weathercode")
    current_info = _weather_code_info(current_code)

    # Build hourly array
    hourly_raw = data.get("hourly", {})
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

    # Build daily array
    daily_raw = data.get("daily", {})
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
            "code": code,
            "icon": info["icon"],
            "desc": info["description"],
        })

    return jsonify({
        "current": {
            "temp": current.get("temperature"),
            "wind": current.get("windspeed"),
            "wind_dir": current.get("winddirection"),
            "code": current_code,
            "icon": current_info["icon"],
            "desc": current_info["description"],
            "is_day": current.get("is_day"),
            "time": current.get("time"),
        },
        "hourly": hourly,
        "daily": daily,
    })
