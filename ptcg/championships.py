"""Client for championships.pokemon.com (Regionals, Specials, Internationals).

The site's own front end reads /api/events.json, which returns every
Championship Series event for the current season in every supported
locale. Only the en-us rows are kept here.

Dates arrive as display strings ("Sept. 18-20", "Oct. 31 - Nov. 1") and
the year field is the *season*, not the calendar year: season 2027 starts
in autumn 2026. Both are resolved into real dates below.
"""

from __future__ import annotations

import re

from . import http

EVENTS_URL = "https://championships.pokemon.com/api/events.json?locale=en-us"
SITE = "https://championships.pokemon.com"

_HEADERS = {
    "Accept": "application/json",
    "Referer": SITE + "/en-us/events",
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A Play! Pokémon season runs roughly August -> July and is named for the
# calendar year it ends in.
_SEASON_ROLLOVER_MONTH = 8


def _calendar_year(month, season_year):
    return season_year - 1 if month >= _SEASON_ROLLOVER_MONTH else season_year


def parse_date_range(display, season_year):
    """Turn a display range into ``(start_date, end_date)`` ISO strings.

    Handles "Sept. 18-20", "Oct. 31 - Nov. 1", "May 8-9" and single days.
    Returns ``(None, None)`` if the string can't be understood, so one odd
    row never breaks the whole build.
    """
    if not display:
        return None, None

    # Normalise the dashes the site uses (en dash, em dash) to a plain one.
    text = display.replace("–", "-").replace("—", "-").replace("−", "-")
    # "Sept. 18-20" -> tokens of (month?, day)
    parts = [p.strip() for p in text.split("-")]

    parsed = []
    current_month = None
    for part in parts:
        match = re.match(r"^([A-Za-z]+)?\.?\s*(\d{1,2})", part.strip())
        if not match:
            continue
        name, day = match.group(1), int(match.group(2))
        if name:
            current_month = _MONTHS.get(name[:4].lower().rstrip(".")) or _MONTHS.get(
                name[:3].lower()
            )
        if current_month is None:
            continue
        parsed.append((current_month, day))

    if not parsed:
        return None, None

    start_month, start_day = parsed[0]
    end_month, end_day = parsed[-1]
    start_year = _calendar_year(start_month, season_year)
    end_year = _calendar_year(end_month, season_year)
    # A range that wraps December into January stays inside one season.
    if end_month < start_month:
        end_year = start_year + 1

    try:
        start = "%04d-%02d-%02d" % (start_year, start_month, start_day)
        end = "%04d-%02d-%02d" % (end_year, end_month, end_day)
    except (TypeError, ValueError):
        return None, None
    return start, end


def fetch_events():
    """Return the en-us Championship Series rows, dates resolved."""
    data = http.get_json(EVENTS_URL, headers=_HEADERS)

    events = []
    for item in data.get("items", []):
        if item.get("locale_s") != "en-us":
            continue
        try:
            season_year = int(item.get("year_s", ""))
        except ValueError:
            continue

        start, end = parse_date_range(item.get("displayDateRange_s", ""), season_year)
        if not start:
            continue

        events.append(
            {
                "name": item.get("eventName_s", ""),
                "start": start,
                "end": end,
                "display_range": item.get("displayDateRange_s", ""),
                "location": item.get("eventLocation_s", ""),
                "type": item.get("type_s", ""),
                "region": item.get("region_s", ""),
                "season": season_year,
                "streaming": item.get("isStreaming_b") == "true",
                "url": SITE + item.get("uRL_s", ""),
            }
        )
    return events
