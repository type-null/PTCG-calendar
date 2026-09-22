"""City -> coordinates, so Championship events can be sorted by distance.

The Championship Series feed gives a city name but no coordinates. They
are looked up once via OpenStreetMap's Nominatim and cached on disk, so a
normal update makes no geocoding requests at all. A lookup that fails is
not an error: the event simply has no distance and sorts last.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse

from . import http

NOMINATIM = "https://nominatim.openstreetmap.org/search"
EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


class Geocoder:
    def __init__(self, cache_path="data/geocode_cache.json", enabled=True):
        self.cache_path = cache_path
        self.enabled = enabled
        self.dirty = False
        try:
            with open(cache_path, encoding="utf-8") as fh:
                self.cache = json.load(fh)
        except (OSError, ValueError):
            self.cache = {}

    def lookup(self, place):
        """Return ``(lat, lon)`` for a place name, or ``None``."""
        if not place:
            return None
        key = place.strip().lower()
        if key in self.cache:
            hit = self.cache[key]
            return tuple(hit) if hit else None
        if not self.enabled:
            return None

        query = urllib.parse.urlencode(
            {"q": place, "format": "json", "limit": 1}
        )
        result = None
        try:
            # Nominatim asks for <=1 request/second and a descriptive agent.
            time.sleep(1.1)
            rows = http.get_json(
                NOMINATIM + "?" + query,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PTCG-calendar/1.0 (personal local event calendar)",
                },
                attempts=2,
            )
            if rows:
                result = (float(rows[0]["lat"]), float(rows[0]["lon"]))
        except Exception:
            result = None

        self.cache[key] = list(result) if result else None
        self.dirty = True
        return result

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, indent=1, sort_keys=True)
        self.dirty = False
