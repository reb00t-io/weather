"""Quart blueprint exposing /api/events.

Auth is enforced by the global before_request hook in main.py — any /api/*
path requires Bearer auth. This module just parses query params, calls the
service, and shapes the JSON response.
"""

from __future__ import annotations

import re

from quart import Blueprint, jsonify, request

from . import service

events_bp = Blueprint("events", __name__)

DEFAULT_DAYS = service.DEFAULT_DAYS
MAX_DAYS = 30


_REGION_PREFIXES = (
    "Hansestadt ",
    "Landeshauptstadt ",
    "Kreisstadt ",
    "Universitätsstadt ",
    "Stadt ",
)


def _resolve_region(name: str) -> str:
    """Reduce a place name to its broader region key — same logic as
    weather_api._resolve_city so 'Berlin-Marzahn, Berlin' → 'Berlin'.

    Strips German place-name prefixes the geocoder loves to add
    ('Hansestadt Salzwedel' → 'Salzwedel', 'Landeshauptstadt München'
    → 'München') so the region matches what our sources tag events
    with."""
    name = name.split(",")[0].strip()
    for prefix in _REGION_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    name = re.split(r"[-–]", name)[0].strip()
    return name


@events_bp.route("/api/events")
async def list_events():
    raw = (request.args.get("region") or request.args.get("city") or "").strip()
    if not raw:
        return jsonify({"error": "region required"}), 400
    region = _resolve_region(raw)

    try:
        days = int(request.args.get("days", DEFAULT_DAYS))
    except ValueError:
        return jsonify({"error": "days must be an integer"}), 400
    days = max(1, min(days, MAX_DAYS))

    events = service.query_events(region, days=days)
    return jsonify({
        "region": region,
        "days": days,
        "count": len(events),
        "events": [e.to_json() for e in events],
    })
