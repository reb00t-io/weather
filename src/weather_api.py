"""Weather API routes using Open-Meteo (forecasts) and Bright Sky / DWD (current observations)."""

import asyncio
import json as _json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from quart import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
weather_bp = Blueprint("weather", __name__)

UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")

# Wikipedia requires a descriptive User-Agent per their robot policy
# (https://w.wiki/4wJS) — anonymous requests now return 403.
WIKIPEDIA_UA = "WeatherApp/1.0 (https://weather.reb00t.io; marko@rosenmueller.de)"

# ── Persistent city image cache (SQLite) ──────────────────
_CACHE_TTL = 86400  # 24 hours


class CityImageCache:
    """SQLite-backed persistent cache for city images.

    Concurrency: Uses WAL mode so readers never block. A threading lock
    serialises writes — safe under Quart's async event loop because
    sqlite3 ops hit local disk and complete in < 1 ms.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = os.environ.get(
                "CITY_IMAGE_CACHE_PATH",
                str(Path.home() / ".cache" / "weather" / "city_images.db"),
            )
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS city_images (
                        cache_key   TEXT PRIMARY KEY,
                        url         TEXT,
                        attribution TEXT,
                        fetched_at  REAL NOT NULL
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def get(self, key: str, ttl: float = _CACHE_TTL) -> dict | None:
        """Return cached entry if present and fresh, else None."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT url, attribution, fetched_at FROM city_images WHERE cache_key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        url, attr_json, fetched_at = row
        if (time.time() - fetched_at) >= ttl:
            return None  # expired
        return {
            "url": url,
            "attribution": _json.loads(attr_json) if attr_json else None,
            "ts": fetched_at,
        }

    def put(self, key: str, url: str | None, attribution: dict | None) -> None:
        """Insert or replace a cache entry."""
        attr_json = _json.dumps(attribution) if attribution else None
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO city_images (cache_key, url, attribution, fetched_at) "
                    "VALUES (?, ?, ?, ?)",
                    (key, url, attr_json, time.time()),
                )
                conn.commit()
            finally:
                conn.close()


_city_image_cache = CityImageCache()


def _resolve_city(city: str) -> str:
    """Simplify city name: strip district suffixes and comma-separated parts."""
    city = city.split(",")[0].strip()
    city = re.split(r"[-–]", city)[0].strip()
    return city


def _weather_search_term(weather: str | None, is_night: bool) -> str:
    """Map weather/night params to Unsplash search terms."""
    if is_night:
        return "night skyline"
    if weather == "sun":
        return "sunny"
    if weather == "cloud":
        return "cloudy overcast"
    if weather == "rain":
        return "rain rainy"
    if weather == "snow":
        return "snow winter"
    return "skyline cityscape"


OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_AQI = "https://air-quality-api.open-meteo.com/v1/air-quality"
BRIGHTSKY_CURRENT = "https://api.brightsky.dev/current_weather"
BRIGHTSKY_WEATHER = "https://api.brightsky.dev/weather"
DWD_WARNINGS = "https://api.brightsky.dev/alerts"

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


async def _fetch_brightsky_hourly(session, lat, lon, hours_ahead=49):
    """Fetch hourly forecast (MOSMIX) from Bright Sky for the next ~48h.

    MOSMIX is DWD's official station-anchored statistical forecast — for
    German locations it's typically more accurate in the short range than
    raw model output, since it's post-processed against actual station
    observations. We use it to overlay Open-Meteo's ICON values for the
    next 48 hours.
    """
    now = datetime.now(timezone.utc)
    last = now + timedelta(hours=hours_ahead)
    params = {
        "lat": lat,
        "lon": lon,
        "date": now.strftime("%Y-%m-%dT%H:00"),
        "last_date": last.strftime("%Y-%m-%dT%H:00"),
        "tz": "Europe/Berlin",
        "units": "dwd",
    }
    try:
        async with session.get(BRIGHTSKY_WEATHER, params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("Bright Sky hourly fetch failed: %s", e)
        return None


def _index_brightsky_hourly(bs_hourly):
    """Index MOSMIX records by 'YYYY-MM-DDTHH:00' to match Open-Meteo keys."""
    if not bs_hourly or "weather" not in bs_hourly:
        return {}
    out = {}
    for w in bs_hourly["weather"]:
        ts = w.get("timestamp")
        if not ts or len(ts) < 13:
            continue
        # "2026-04-26T17:00:00+02:00" -> "2026-04-26T17:00"
        out[ts[:13] + ":00"] = w
    return out


def _build_daily(daily_raw):
    """Convert an Open-Meteo daily payload into our daily[] list."""
    times = daily_raw.get("time", [])
    n = len(times)
    out = []
    for i, t in enumerate(times):
        code = daily_raw.get("weathercode", [None] * n)[i]
        info = _weather_code_info(code)
        out.append({
            "date": t,
            "temp_max": daily_raw.get("temperature_2m_max", [])[i],
            "temp_min": daily_raw.get("temperature_2m_min", [])[i],
            "feels_max": daily_raw.get("apparent_temperature_max", [])[i],
            "feels_min": daily_raw.get("apparent_temperature_min", [])[i],
            "precip_sum": daily_raw.get("precipitation_sum", [])[i],
            "precip_prob": daily_raw.get("precipitation_probability_max", [])[i],
            "wind_max": daily_raw.get("windspeed_10m_max", [])[i],
            "wind_dir": daily_raw.get("winddirection_10m_dominant", [])[i],
            "sunrise": daily_raw.get("sunrise", [])[i],
            "sunset": daily_raw.get("sunset", [])[i],
            "uv_max": daily_raw.get("uv_index_max", [])[i],
            "sun_hours": round((daily_raw.get("sunshine_duration", [0] * n)[i] or 0) / 3600, 1),
            "cloud_mean": daily_raw.get("cloud_cover_mean", [None] * n)[i],
            "code": code,
            "icon": info["icon"],
            "desc": info["description"],
        })
    return out


def _icon_from_mosmix(bs_w):
    """Derive (desc, icon) from MOSMIX numeric fields.

    Bright Sky's `icon` string is coarse — it has no "mostly-clear"
    variant, so 30-40% cloud cover always lands on "partly-cloudy-day".
    Using `cloud_cover` directly recovers the finer WMO-aligned buckets:

        <12%  oktas 0-1  clear
        <38%  oktas 2-3  mostly-clear
        <75%  oktas 4-5  partly-cloudy
        >=75% oktas 6-8  overcast
    """
    cond = (bs_w.get("condition") or "dry").lower()
    cloud = bs_w.get("cloud_cover")
    precip = bs_w.get("precipitation") or 0

    if cond == "thunderstorm":
        return ("Gewitter mit Hagel", "thunderstorm-hail") if precip > 0 else ("Gewitter", "thunderstorm")
    if cond == "snow":
        if precip < 0.3:
            return ("Leichter Schneefall", "snow-light")
        if precip >= 2.0:
            return ("Starker Schneefall", "snow-heavy")
        return ("Schneefall", "snow")
    if cond == "sleet":
        return ("Schneeregen", "freezing-rain")
    if cond == "hail":
        return ("Gewitter mit Hagel", "thunderstorm-hail")
    if cond == "rain":
        if precip < 0.5:
            return ("Leichter Regen", "rain-light")
        if precip >= 2.5:
            return ("Starker Regen", "rain-heavy")
        return ("Regen", "rain")
    if cond == "fog":
        return ("Nebel", "fog")

    if cloud is None:
        return None
    if cloud < 12:
        return ("Klar", "clear")
    if cloud < 38:
        return ("Überwiegend klar", "mostly-clear")
    if cloud < 75:
        return ("Teilweise bewölkt", "partly-cloudy")
    return ("Bewölkt", "overcast")


def _overlay_mosmix(hourly, bs_index):
    """Replace ICON values with MOSMIX where Bright Sky has data."""
    for h in hourly:
        bs_w = bs_index.get(h["time"])
        if not bs_w:
            continue
        for src_key, dst_key in (
            ("temperature", "temp"),
            ("precipitation_probability", "precip_prob"),
            ("precipitation", "precip"),
            ("wind_speed", "wind"),
            ("wind_direction", "wind_dir"),
            ("relative_humidity", "humidity"),
            ("cloud_cover", "cloud"),
        ):
            v = bs_w.get(src_key)
            if v is not None:
                h[dst_key] = v
        mapped = _icon_from_mosmix(bs_w)
        if mapped:
            desc, our_icon = mapped
            h["icon"] = our_icon
            h["desc"] = desc
        h["source"] = "mosmix"


async def _fetch_aqi(session, lat, lon):
    """Fetch air quality from Open-Meteo."""
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "european_aqi,pm2_5,pm10,nitrogen_dioxide,ozone",
            "timezone": "Europe/Berlin",
        }
        async with session.get(OPEN_METEO_AQI, params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("AQI fetch failed: %s", e)
        return None


async def _fetch_warnings(session, lat, lon):
    """Fetch DWD weather warnings from Bright Sky alerts API."""
    try:
        params = {"lat": lat, "lon": lon, "tz": "Europe/Berlin"}
        async with session.get(DWD_WARNINGS, params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("DWD warnings fetch failed: %s", e)
        return None


def _parse_brightsky_current(bs_data, is_day_fallback=None):
    """Parse Bright Sky current_weather response into our format."""
    if not bs_data or "weather" not in bs_data:
        return None

    w = bs_data["weather"]
    if w.get("temperature") is None:
        return None

    bs_icon = w.get("icon") or ""
    mapped = BRIGHTSKY_ICON_MAP.get(bs_icon)
    if not mapped:
        return None
    desc, our_icon = mapped

    if bs_icon.endswith("-day"):
        is_day = 1
    elif bs_icon.endswith("-night"):
        is_day = 0
    else:
        is_day = is_day_fallback

    cloud = w.get("cloud_cover")
    # Bright Sky's `icon` string is coarse — it has no "mostly-clear"
    # variant, so 12-38% cloud cover lands on "partly-cloudy-day"
    # and the card looks more clouded than reality. When we have a
    # numeric cloud_cover, rebucket the cloud-family icons against
    # the WMO-aligned thresholds used by the hourly path.
    _CLOUD_FAMILY = {
        "clear-day", "clear-night",
        "partly-cloudy-day", "partly-cloudy-night",
        "cloudy", "wind",
    }
    if bs_icon in _CLOUD_FAMILY and cloud is not None:
        if cloud < 12:
            desc, our_icon = ("Klar", "clear")
        elif cloud < 38:
            desc, our_icon = ("Überwiegend klar", "mostly-clear")
        elif cloud < 75:
            desc, our_icon = ("Teilweise bewölkt", "partly-cloudy")
        else:
            desc, our_icon = ("Bewölkt", "overcast")

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


def _parse_aqi(aqi_data):
    """Parse Open-Meteo AQI response."""
    if not aqi_data or "current" not in aqi_data:
        return None
    c = aqi_data["current"]
    eaqi = c.get("european_aqi")
    if eaqi is None:
        return None

    # European AQI levels
    if eaqi <= 20:
        level, color = "Gut", "green"
    elif eaqi <= 40:
        level, color = "Mäßig", "yellow"
    elif eaqi <= 60:
        level, color = "Mittel", "orange"
    elif eaqi <= 80:
        level, color = "Schlecht", "red"
    elif eaqi <= 100:
        level, color = "Sehr schlecht", "purple"
    else:
        level, color = "Gefährlich", "maroon"

    return {
        "eaqi": eaqi,
        "level": level,
        "color": color,
        "pm2_5": c.get("pm2_5"),
        "pm10": c.get("pm10"),
        "no2": c.get("nitrogen_dioxide"),
        "o3": c.get("ozone"),
    }


def _parse_warnings(warn_data):
    """Parse Bright Sky DWD alerts response."""
    if not warn_data or "alerts" not in warn_data:
        return []
    alerts = []
    for a in warn_data["alerts"]:
        alerts.append({
            "headline": a.get("headline", ""),
            "description": a.get("description", ""),
            "severity": a.get("severity", ""),
            "event": a.get("event", ""),
            "effective": a.get("effective"),
            "expires": a.get("expires"),
        })
    return alerts


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


async def _search_unsplash(session, city: str, weather: str | None, is_night: bool):
    """Search Unsplash for a weather-aware city photo. Returns (url, attribution) or (None, None)."""
    if not UNSPLASH_ACCESS_KEY:
        return None, None
    term = _weather_search_term(weather, is_night)
    query = f"{city} {term} cityscape"
    try:
        headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
        params = {"query": query, "per_page": 3, "orientation": "landscape"}
        async with session.get(
            "https://api.unsplash.com/search/photos", headers=headers, params=params,
        ) as resp:
            if resp.status != 200:
                logger.debug("Unsplash error %s for query '%s'", resp.status, query)
                return None, None
            data = await resp.json()
            results = data.get("results", [])
            if not results:
                return None, None
            photo = results[0]
            url = photo["urls"]["regular"]
            attribution = {
                "name": photo["user"]["name"],
                "link": photo["links"]["html"],
            }
            return url, attribution
    except Exception as e:
        logger.debug("Unsplash fetch failed: %s", e)
        return None, None


async def _search_wikipedia(session, city: str):
    """Search Wikipedia for a city image. Returns URL or None."""
    headers = {"User-Agent": WIKIPEDIA_UA}
    # Try English first — German Wikipedia tends to return flags / coats-of-arms
    # for German cities (rendered as .svg.png), while English has actual photos.
    for lang in ("en", "de"):
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{city}"
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                orig = data.get("originalimage", {})
                thumb = data.get("thumbnail", {})
                img_src = orig.get("source") or thumb.get("source")
                if not img_src:
                    continue
                # Skip SVGs (and SVGs rendered to PNG, e.g. flags & coats of arms)
                src_lower = img_src.lower()
                if src_lower.endswith(".svg") or ".svg/" in src_lower:
                    continue
                if orig.get("width", 0) < 400 and thumb.get("width", 0) < 400:
                    continue
                # Rewrite to a 1280px-wide thumbnail. Wikimedia rejects
                # arbitrary widths (HTTP 400, "use thumbnail steps"); 1280 is
                # one of the always-allowed standard sizes.
                if "/commons/" in img_src and "/thumb/" not in img_src:
                    img_src = img_src.replace(
                        "/commons/", "/commons/thumb/"
                    ) + "/1280px-" + img_src.rsplit("/", 1)[-1]
                elif "/thumb/" in img_src:
                    parts = img_src.rsplit("/", 1)
                    img_src = parts[0] + "/1280px-" + parts[1].split("px-", 1)[-1]
                return img_src
        except Exception:
            continue
    return None


@weather_bp.route("/api/city-image")
async def city_image():
    """Return a weather-aware image URL for a city with server-side caching."""
    raw_city = request.args.get("city", "").strip()
    if not raw_city:
        return jsonify({"url": None, "attribution": None})

    weather = request.args.get("weather", "").strip() or None
    is_night = request.args.get("is_night", "0") == "1"
    city = _resolve_city(raw_city)

    # Check cache
    cache_key = f"{city}:{weather}:{is_night}"
    cached = _city_image_cache.get(cache_key)
    if cached:
        return jsonify({"url": cached["url"], "attribution": cached["attribution"]})

    async with aiohttp.ClientSession() as s:
        # Try Unsplash first (weather-aware)
        url, attribution = await _search_unsplash(s, city, weather, is_night)

        # Fallback to Wikipedia
        if not url:
            url = await _search_wikipedia(s, city)
            attribution = None

    # Cache the result (even None to avoid repeated failed lookups)
    _city_image_cache.put(cache_key, url, attribution)

    return jsonify({"url": url, "attribution": attribution})


@weather_bp.route("/api/reverse-geocode")
async def reverse_geocode():
    """Reverse geocode lat/lon to city name using Open-Meteo."""
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lon required"}), 400
    # Search for nearest city by using coordinates as a search hint
    # Open-Meteo geocoding doesn't have reverse geocode, so we use a nearby city search
    params = {"latitude": lat, "longitude": lon, "count": 1, "language": "de", "format": "json"}
    async with aiohttp.ClientSession() as s:
        # Use a name-based search with empty name won't work, so use the Bright Sky source
        try:
            bs_params = {"lat": lat, "lon": lon, "tz": "Europe/Berlin"}
            async with s.get(BRIGHTSKY_CURRENT, params=bs_params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sources = data.get("sources", [])
                    if sources:
                        name = sources[0].get("station_name", "")
                        if name:
                            return jsonify({"name": name, "admin1": "", "country": "Deutschland", "lat": lat, "lon": lon})
        except Exception:
            pass
    return jsonify({"name": "Mein Standort", "admin1": "", "country": "", "lat": lat, "lon": lon})


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
            "apparent_temperature_max",
            "apparent_temperature_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "windspeed_10m_max",
            "winddirection_10m_dominant",
            "sunrise",
            "sunset",
            "uv_index_max",
            "sunshine_duration",
            "cloud_cover_mean",
        ]),
        "current_weather": "true",
        "timezone": "Europe/Berlin",
        "forecast_days": 7,
        # DWD ICON seamless: ICON-D2 (2km) for 0-48h, ICON-EU (7km) to ~5d,
        # ICON global beyond. Most accurate model chain for Germany; tops
        # out at ~7.5 days, which is why forecast_days is 7.
        "models": "icon_seamless",
    }

    # Long-range daily forecast for days 8-14, where DWD ICON has no data.
    # Uses Open-Meteo's best_match blend (typically ECMWF/GFS at this horizon).
    om_long_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": om_params["daily"],
        "timezone": "Europe/Berlin",
        "forecast_days": 14,
    }

    # Fetch all data sources in parallel
    async with aiohttp.ClientSession() as s:
        om_data, om_long_data, bs_data, bs_hourly_data, aqi_data, warn_data = await asyncio.gather(
            _fetch_openmeteo(s, om_params),
            _fetch_openmeteo(s, om_long_params),
            _fetch_brightsky(s, lat, lon),
            _fetch_brightsky_hourly(s, lat, lon),
            _fetch_aqi(s, lat, lon),
            _fetch_warnings(s, lat, lon),
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
    # Base from DWD ICON-seamless, then overlay MOSMIX (DWD's official
    # station-anchored statistical forecast) for the next 48h. MOSMIX is
    # typically more accurate than raw model output for the short range.
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
            "source": "icon",
        })

    bs_index = _index_brightsky_hourly(bs_hourly_data)
    if bs_index:
        _overlay_mosmix(hourly, bs_index)

    # ── Daily forecast ──────────────────────────────────────
    # Days 0-6 from DWD ICON-seamless (high accuracy, Germany-focused).
    # Days 7-13 from Open-Meteo best_match (ECMWF/GFS blend) since ICON
    # only forecasts ~7.5 days.
    daily = _build_daily(om_data.get("daily", {}))
    if om_long_data:
        long_daily = _build_daily(om_long_data.get("daily", {}))
        existing_dates = {d["date"] for d in daily}
        for d in long_daily:
            if d["date"] not in existing_dates:
                daily.append(d)

    return jsonify({
        "current": current_out,
        "hourly": hourly,
        "daily": daily,
        "aqi": _parse_aqi(aqi_data),
        "warnings": _parse_warnings(warn_data),
    })
