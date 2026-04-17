# Improvement Plan

All items completed. See git history for details.

## Done
- [x] Map: switched from dark CartoDB to light Voyager tiles (green/beautiful)
- [x] Map: fixed rain forecast layer (RainViewer color scheme 6 for light backgrounds)
- [x] Images: weather-aware city images (sun/cloud/rain/snow + night variants)
- [x] Images: Unsplash API with env var `UNSPLASH_ACCESS_KEY`, Wikipedia fallback
- [x] Images: region resolution (Berlin-Marzahn -> Berlin)
- [x] Images: persistent SQLite cache (24h TTL, survives restarts, configurable via `CITY_IMAGE_CACHE_PATH`)
- [x] Tests: 21 new tests covering all new functionality (44 weather tests, 64 total, all passing)
