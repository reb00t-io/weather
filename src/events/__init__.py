"""Local events module.

Pulls events from a public source (kulturdaten.berlin for now), stores them
in a small SQLite database, and exposes a Quart blueprint at /api/events.

Designed to be self-contained so it can later move to its own service:
- `store.py`     — SQLite persistence (no Quart deps)
- `source_kdb.py` — kulturdaten.berlin client + normalisation
- `service.py`   — orchestration: refresh, query
- `api.py`       — Quart blueprint (the only file with Quart imports)
"""
