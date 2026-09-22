"""Client for the Play! Pokémon Event Locator.

events.pokemon.com/EventLocator is an OutSystems app with no documented
API, so this talks to the same screen-service endpoint its front end uses:

    POST /EventLocator/screenservices/EventLocator/MainFlow/Home/DataActionGetEventList

The request carries two version tokens. ``moduleVersion`` changes whenever
Pokémon redeploys the app and ``apiVersion`` identifies the data action;
both are discovered at runtime (and cached) so a redeploy doesn't break
this script.
"""

from __future__ import annotations

import json
import os
import re

from . import http

BASE = "https://events.pokemon.com/EventLocator/"
ENDPOINT = BASE + "screenservices/EventLocator/MainFlow/Home/DataActionGetEventList"
HOME_SCRIPT = BASE + "scripts/EventLocator.MainFlow.Home.mvc.js"
VERSION_URL = BASE + "moduleservices/moduleversioninfo"

# Known-good values, used until the server tells us they went stale.
FALLBACK_MODULE_VERSION = "jthyeHtrvAHwGKhOBl+HOA"
FALLBACK_API_VERSION = "lbKy3fbhrLZ5eb1JFcZNZw"

_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://events.pokemon.com",
    "Referer": BASE,
    "X-CSRFToken": "",
}


def _load_tokens(cache_path):
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        return cached["moduleVersion"], cached["apiVersion"]
    except (OSError, KeyError, ValueError):
        return FALLBACK_MODULE_VERSION, FALLBACK_API_VERSION


def _save_tokens(cache_path, module_version, api_version):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"moduleVersion": module_version, "apiVersion": api_version}, fh, indent=2
        )


def _discover_tokens(session):
    """Re-read both version tokens from the live app."""
    module_version = session.get_json(
        VERSION_URL, headers={"Accept": "application/json", "Referer": BASE}
    )["versionToken"]

    script = session.request(HOME_SCRIPT, headers={"Referer": BASE})
    match = re.search(
        r'"DataActionGetEventList",\s*"[^"]+",\s*"([^"]+)"',
        script,
    )
    api_version = match.group(1) if match else FALLBACK_API_VERSION
    return module_version, api_version


def _payload(module_version, api_version, lat, lon, radius_miles, start, end, max_records):
    lat, lon = str(lat), str(lon)
    return {
        "versionInfo": {
            "moduleVersion": module_version,
            "apiVersion": api_version,
        },
        "viewName": "MainFlow.Home",
        "screenData": {
            "variables": {
                "latitude": lat,
                "longitude": lon,
                "range": str(radius_miles),
                "iskm": False,
                "locale": "en-US",
                "filters": "",
                "SortDistance": True,
                "MaxRecords": max_records,
                "Count": 0,
            }
        },
        "clientVariables": {
            "Latitude": lat,
            "Longitude": lon,
            "Range": radius_miles,
            "IsKm": False,
            "StartDate": start,
            "EndDate": end,
            "UserLocale": "en-US",
            "IsShowEvents": True,
            "IsSearch": True,
            "FilterEventTypes": "",
            "PremierEventsIdList": "",
            "MaxDateLimit": 180,
            "CurrTab": 0,
            "IsChampionshipSeries": False,
            "IsShowMap": False,
            "AreEventsBeingSearched": False,
            "IsOnReadyFinished": True,
            "LocationName": "",
        },
    }


def fetch_events(lat, lon, radius_miles, start, end, max_records=2000,
                 token_cache="data/tokens.json"):
    """Return the raw event records the locator has for this area and window.

    ``start`` and ``end`` are ``YYYY-MM-DD`` strings.
    """
    module_version, api_version = _load_tokens(token_cache)

    # Arrive at the app the way a browser does before its XHR fires.
    session = http.Session()
    session.warm(BASE + "?locale=en-US")

    def call(mv, av):
        body = _payload(mv, av, lat, lon, radius_miles, start, end, max_records)
        return session.get_json(ENDPOINT, data=body, headers=_HEADERS)

    response = call(module_version, api_version)
    version_info = response.get("versionInfo", {})

    # The app was redeployed since we last looked: refresh and retry once.
    if version_info.get("hasModuleVersionChanged") or version_info.get(
        "hasApiVersionChanged"
    ):
        module_version, api_version = _discover_tokens(session)
        response = call(module_version, api_version)

    _save_tokens(token_cache, module_version, api_version)
    return response["data"]["EventList"]["List"]
