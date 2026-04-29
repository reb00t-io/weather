"""Event classification — heuristic baseline + optional Claude refinement.

The heuristic runs at refresh time, deterministic and free, so the Events
tab is usable immediately. The AI layer (claude-haiku-4-5) refines the
classification asynchronously when ANTHROPIC_API_KEY is configured. Each
event is enriched at most once per source-side update — `enriched_at` on
the row stops re-classification on subsequent refreshes.

Categories (keep this list short — too many categories defeats filtering):
    music, stage, art, family, market, sports, talk, festival, civic, other

interest_score: 0..3
    0 = noise / pure admin (default-hidden)
    1 = niche local
    2 = generally interesting cultural/community event
    3 = headline / festival / big draw
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable

import httpx

from .store import Event, now_ts

logger = logging.getLogger(__name__)

CATEGORIES = (
    "music", "stage", "art", "family",
    "market", "sports", "talk", "festival", "civic", "other",
)


@dataclass
class Classification:
    category: str
    interest_score: int
    is_civic: bool


# ── Heuristic classifier ────────────────────────────────────────────────────

# Patterns are checked in order; first match wins. Civic patterns run first
# because their titles often contain decoy words ("Theater im Bürgersaal").
# Word boundaries are German-aware (\b doesn't match umlauts, but the
# patterns below mostly key on Latin-only stems so it's fine).

_CIVIC = re.compile(
    r"\b("
    r"sprechstunde|beratung|berufsberatung|"
    r"beteiligung der öffentlichkeit|bürgerversammlung|"
    r"bebauungsplan|bezirksverordnetenversammlung|bvv|"
    r"sitzung des|bürgersprechstunde|"
    r"standort der mobilen wache|mobile wache|"
    r"energieberatung|stationäre energieberatung|"
    r"infotag(e)?|info-tag|infoabend|info-abend|infoveranstaltung|"
    r"einwohnerfragestunde|einwohnerversammlung|"
    r"bürgerbeteiligung|öffentliche auslegung|"
    r"einbürgerung|behörden(gang|sprechstunde)"
    r")\b",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[re.Pattern[str], str, int]] = [
    # Music — mid-to-high interest
    (re.compile(r"\b(konzert|live[-\s]?musik|jazz|klassik|symphoni|"
                r"chor|orchester|recital|klavierabend|"
                r"dj[-\s]?set|clubnacht|electronic music|techno)\b",
                re.IGNORECASE), "music", 2),

    # Stage / theatre / dance
    (re.compile(r"\b(theater|bühne|schauspiel|comedy|"
                r"kabarett|tanz|dance|performance|"
                r"oper(n|ette)?|musical|premiere|aufführung)\b",
                re.IGNORECASE), "stage", 2),

    # Art / exhibitions
    (re.compile(r"\b(ausstellung|vernissage|finissage|"
                r"galerie|museum|kunst|gemälde|"
                r"skulptur|installation|fotoausstellung|"
                r"führung)\b", re.IGNORECASE), "art", 2),

    # Festival / big draws — score 3
    (re.compile(r"\b(festival|stadtfest|straßenfest|"
                r"karneval|kiezfest|sommerfest|jubiläum|"
                r"lange nacht der)\b", re.IGNORECASE), "festival", 3),

    # Markets / food
    (re.compile(r"\b(flohmarkt|wochenmarkt|streetfood|"
                r"food[-\s]?market|kunsthandwerk(s)?markt|"
                r"weihnachtsmarkt|ostermarkt|trödelmarkt)\b",
                re.IGNORECASE), "market", 2),

    # Family / kids
    (re.compile(r"\b(kinder|familien|kita|"
                r"kindertheater|kinderkonzert|"
                r"vorlesen|lesemäuse|familiennachmittag|"
                r"basteln|kreativ|spielenachmittag)\b",
                re.IGNORECASE), "family", 2),

    # Sports
    (re.compile(r"\b(turnier|liga|spieltag|marathon|"
                r"halbmarathon|fußball|basketball|"
                r"volleyball|hockey|tennis|laufen|"
                r"sportfest)\b", re.IGNORECASE), "sports", 2),

    # Talk / lecture / reading
    (re.compile(r"\b(lesung|buchvorstellung|vortrag|"
                r"podiumsdiskussion|panel|workshop|"
                r"diskussion|gespräch|symposium)\b",
                re.IGNORECASE), "talk", 1),
]

# Niche-but-cultural patterns get demoted to score 1.
_NICHE = re.compile(
    r"\b(sprachcafé|stricken|häkeln|nähen|"
    r"go[-\s]?gruppe|schach|mahjong|skat|"
    r"computer[-\s]?(hilfe|sprechstunde)|"
    r"yoga|meditation|"
    r"flanier|spaziergang|kiezspaziergang)\b",
    re.IGNORECASE,
)


def classify_heuristic(title: str, venue: str | None = None) -> Classification:
    """Deterministic regex-based classifier. Always returns a result.

    Used as the baseline at refresh time so the tab works without AI."""
    text = f"{title} {venue or ''}"

    if _CIVIC.search(text):
        return Classification("civic", 0, True)

    for pat, category, score in _PATTERNS:
        if pat.search(text):
            if _NICHE.search(text):
                score = max(score - 1, 1)
            return Classification(category, score, False)

    if _NICHE.search(text):
        return Classification("other", 1, False)

    return Classification("other", 1, False)


# ── AI refinement ───────────────────────────────────────────────────────────

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_VERSION = "2023-06-01"
BATCH_SIZE = 50

_AI_PROMPT = (
    "You categorize Berlin event listings for a daily-use weather/events app. "
    "The user wants to find culturally interesting things to do — concerts, "
    "exhibitions, theatre, family outings, markets, festivals — not "
    "bureaucratic notices.\n\n"
    "Categories (pick exactly one):\n"
    "- music: concerts, DJs, choirs, live bands\n"
    "- stage: theatre, dance, comedy, performance, opera\n"
    "- art: exhibitions, vernissages, museum events, guided art tours\n"
    "- family: events aimed at children and families\n"
    "- market: street/food/flea/craft markets\n"
    "- sports: sports events, tournaments, runs\n"
    "- talk: lectures, readings, panels, public workshops\n"
    "- festival: festivals, neighbourhood/street parties, big celebrations\n"
    "- civic: bureaucratic/admin items — public hearings, planning "
    "consultations, mobile police stations, council meetings, "
    "social-service consultation hours, energy advice sessions, info days\n"
    "- other: doesn't fit the above\n\n"
    "interest_score (general-public interest):\n"
    "0 = noise; civic admin; only relevant if you're directly affected. "
    "All civic items get 0 unless they're a major public event.\n"
    "1 = niche/local; only interesting if you live in that Kiez or share "
    "the specific hobby (e.g. recurring chess/knitting groups, hyper-local "
    "library activities for kids that aren't open events)\n"
    "2 = generally interesting cultural/community event most users would "
    "consider attending (regular exhibitions, theatre, concerts, lesungen, "
    "open guided tours)\n"
    "3 = headline / festival / can't-miss / large public event\n\n"
    "is_civic = true for any pure bureaucratic/admin item.\n\n"
    "Output: ONE JSON object per line, in input order, with keys "
    "{id, category, interest_score, is_civic}. No prose, no code fences. "
    "Example line: "
    '{"id":"E_X","category":"art","interest_score":2,"is_civic":false}'
)


def _format_batch(events: list[Event]) -> str:
    lines = []
    for e in events:
        lines.append(json.dumps({
            "id": e.id,
            "title": e.title,
            "venue": e.venue or "",
        }, ensure_ascii=False))
    return "\n".join(lines)


def _parse_response(text: str) -> dict[str, Classification]:
    """Parse JSONL response into {id: Classification}. Tolerates blank
    lines, surrounding prose, and code fences."""
    out: dict[str, Classification] = {}
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = obj.get("id")
        cat = obj.get("category")
        score = obj.get("interest_score")
        civic = obj.get("is_civic")
        if not eid or cat not in CATEGORIES or not isinstance(score, int):
            continue
        out[eid] = Classification(
            category=cat,
            interest_score=max(0, min(3, score)),
            is_civic=bool(civic),
        )
    return out


async def classify_with_ai(
    events: list[Event],
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Classification]:
    """Send events to Claude Haiku in batches, return {id: Classification}.

    Returns an empty dict if no API key is configured. Failed batches are
    logged and skipped — partial results are returned rather than raising."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not events:
        return {}

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=60.0)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    results: dict[str, Classification] = {}
    try:
        for start in range(0, len(events), BATCH_SIZE):
            batch = events[start:start + BATCH_SIZE]
            user_msg = (
                "Classify these events:\n\n" + _format_batch(batch)
            )
            body = {
                "model": ANTHROPIC_MODEL,
                "max_tokens": 4096,
                "system": _AI_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            }
            try:
                r = await client.post(ANTHROPIC_URL, headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
                text = "".join(
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                )
                parsed = _parse_response(text)
                results.update(parsed)
                logger.info(
                    "events.enrich.ai: batch %d-%d → %d/%d classified",
                    start, start + len(batch), len(parsed), len(batch),
                )
            except Exception:
                logger.exception(
                    "events.enrich.ai: batch %d-%d failed; skipping",
                    start, start + len(batch),
                )
    finally:
        if own_client:
            await client.aclose()

    return results


# ── Apply classification to events ──────────────────────────────────────────

def apply_heuristic(events: Iterable[Event]) -> list[Event]:
    """Set category/interest_score/is_civic on each event based on the
    heuristic classifier. Does NOT set enriched_at — that's reserved for
    the AI layer so re-runs of the heuristic don't block AI refinement."""
    out = []
    for e in events:
        c = classify_heuristic(e.title, e.venue)
        e.category = c.category
        e.interest_score = c.interest_score
        e.is_civic = c.is_civic
        out.append(e)
    return out


def apply_heuristic_if_missing(events: Iterable[Event]) -> list[Event]:
    """Like apply_heuristic, but only fills fields the source didn't already
    set. Used for sources (e.g. Ticketmaster) that ship their own taxonomy."""
    out = []
    for e in events:
        if e.category is None or e.interest_score is None or e.is_civic is None:
            c = classify_heuristic(e.title, e.venue)
            if e.category is None:
                e.category = c.category
            if e.interest_score is None:
                e.interest_score = c.interest_score
            if e.is_civic is None:
                e.is_civic = c.is_civic
        out.append(e)
    return out


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def now() -> float:
    return now_ts()
