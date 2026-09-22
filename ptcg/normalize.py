"""Fold both sources into one event shape the calendar page can render."""

from __future__ import annotations

import re

from .geocode import haversine_miles

PRODUCT_LABELS = {
    "tcg": "TCG",
    "vg": "Video Game",
    "pgo": "Pokémon GO",
    "go": "Pokémon GO",
    "unite": "UNITE",
    "tcgl": "TCG Live",
    "tcgp": "TCG Pocket",
}

# The feed writes Pokémon GO as "pgo"; the page's colours and filters use
# one key per game, so fold the variants together.
PRODUCT_ALIASES = {"go": "pgo"}

CHAMPIONSHIP_TYPE_LABELS = {
    "regional": "Regional Championships",
    "international": "International Championships",
    "worlds": "World Championships",
    "special": "Special Championships",
}

REGION_LABELS = {
    "northamerica": "North America",
    "europe": "Europe",
    "latinamerica": "Latin America",
    "oceania": "Oceania",
    "asia": "Asia",
}


def _strip_zone(stamp):
    """Locator times are venue wall-clock even though they carry a 'Z'.

    An event listed as 18:30:00Z starts at 6:30 PM at the store, so the
    zone marker is dropped rather than converted.
    """
    if not stamp:
        return None
    text = stamp.strip()
    text = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", text)
    return text or None


def _admission(value):
    """The feed writes free entry as "0"; say so in words."""
    text = (value or "").strip()
    if text in {"0", "0.00", "$0", "$0.00"}:
        return "Free"
    return text


def _split_address(full_address):
    """Pull city and state out of a locator address string."""
    parts = [p.strip() for p in (full_address or "").split(",") if p.strip()]
    city, state = "", ""
    if len(parts) >= 3:
        city = parts[-3]
        state_zip = parts[-2].split()
        if state_zip:
            state = state_zip[0]
    elif len(parts) == 2:
        city = parts[0]
    return city.title(), state.upper()


ACRONYMS = {"TCG", "CCG", "GO", "LLC", "II", "III", "NC", "SC", "USA"}


def _title(text):
    """Store names arrive shouted; make them readable but keep acronyms."""
    if not text:
        return ""
    if not text.isupper():
        return text
    return " ".join(
        word if word in ACRONYMS else word.capitalize() for word in text.split()
    )


def from_locator(records, center_lat, center_lon):
    events = []
    for record in records:
        raw = record.get("Events", {})
        address = raw.get("Address", {}) or {}
        start = _strip_zone(raw.get("Start_date"))
        if not start:
            continue

        try:
            lat = float(address.get("Latitude") or "nan")
            lon = float(address.get("Longitude") or "nan")
        except ValueError:
            lat = lon = float("nan")

        distance = None
        if lat == lat and lon == lon:  # not NaN
            distance = round(haversine_miles(center_lat, center_lon, lat, lon), 1)

        city, state = _split_address(address.get("Full_address"))
        products = [
            PRODUCT_ALIASES.get(p, p)
            for p in (raw.get("Products", {}) or {}).get("List", [])
            if p
        ]

        venue = _title(address.get("Name", ""))
        event_type = record.get("EventTypeName") or raw.get("Activity_type", "")
        # Most weekly League sessions are published with no name at all.
        name = (raw.get("Name") or "").strip()
        generic = not name
        if generic:
            name = event_type or "Play! Pokémon event"

        events.append(
            {
                "id": raw.get("Guid") or raw.get("Display_id"),
                "source": "locator",
                "name": name,
                "generic": generic,
                "start": start,
                "end": None,
                "date": start[:10],
                "end_date": start[:10],
                "time": start[11:16] if len(start) >= 16 else "",
                "all_day": False,
                "type": event_type,
                "activity_type": raw.get("Activity_type", ""),
                "category": raw.get("Category", ""),
                "products": products,
                "product_labels": [PRODUCT_LABELS.get(p, p.upper()) for p in products],
                "status": raw.get("Status", ""),
                "venue": venue,
                "address": address.get("Full_address", ""),
                "city": city,
                "state": state,
                "lat": lat if lat == lat else None,
                "lon": lon if lon == lon else None,
                "distance_mi": distance,
                "admission": _admission(raw.get("Admission")),
                "details": (raw.get("Details") or "").strip(),
                "website": raw.get("Event_website") or "",
                "registration_url": raw.get("Third_party_registration_website") or "",
                "registration_start": _strip_zone(raw.get("Registration_start")),
                "registration_end": _strip_zone(raw.get("Registration_end")),
                "email": (raw.get("Contact_information", {}) or {}).get("Email", ""),
                "phone": (raw.get("Contact_information", {}) or {}).get("Phone", ""),
                "organizer": _title(
                    (raw.get("ActivityGroup", {}) or {}).get("Display_name", "")
                ),
                "display_id": raw.get("Display_id", ""),
                "region": "",
            }
        )
    return events


def from_championships(records, center_lat, center_lon, geocoder=None):
    events = []
    for record in records:
        name = record.get("name", "")
        kind = record.get("type", "")
        # "Special Championships" are filed under type "regional" upstream.
        if "Special Championships" in name:
            kind = "special"

        distance = None
        lat = lon = None
        if geocoder is not None:
            found = geocoder.lookup(record.get("location", ""))
            if found:
                lat, lon = found
                distance = round(haversine_miles(center_lat, center_lon, lat, lon), 1)

        events.append(
            {
                "id": record.get("url", name),
                "source": "championships",
                "name": name,
                "generic": False,
                "start": record["start"],
                "end": record.get("end"),
                "date": record["start"],
                "end_date": record.get("end") or record["start"],
                "time": "",
                "all_day": True,
                "type": CHAMPIONSHIP_TYPE_LABELS.get(kind, "Championship Series"),
                "activity_type": "championship",
                "category": kind,
                "products": ["tcg", "vg", "go", "unite"],
                "product_labels": ["TCG", "Video Game", "Pokémon GO", "UNITE"],
                "status": "sanctioned",
                "venue": record.get("location", ""),
                "address": record.get("location", ""),
                "city": record.get("location", "").split(",")[0].strip(),
                "state": "",
                "lat": lat,
                "lon": lon,
                "distance_mi": distance,
                "admission": "",
                "details": "%s · %s"
                % (record.get("display_range", ""), record.get("location", "")),
                "website": record.get("url", ""),
                "registration_url": "",
                "registration_start": None,
                "registration_end": None,
                "email": "",
                "phone": "",
                "organizer": "The Pokémon Company International",
                "display_id": "",
                "region": REGION_LABELS.get(
                    record.get("region", ""), record.get("region", "")
                ),
                "streaming": record.get("streaming", False),
            }
        )
    return events
