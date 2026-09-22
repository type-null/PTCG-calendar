#!/usr/bin/env python3
"""Refresh the local Pokémon event calendar.

    python3 update.py                 # refresh everything, rebuild calendar.html
    python3 update.py --offline       # rebuild the page from the last fetch
    python3 update.py --radius 75     # widen the search for one run
    python3 update.py --open          # open the result in a browser

Raw responses are kept under data/ so a rebuild never needs the network,
and a failed fetch falls back to the last good copy instead of leaving
you with an empty calendar.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta

from ptcg import build, championships, locator, normalize
from ptcg.geocode import Geocoder
from ptcg.http import AppRejected, BotChallenge

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
RAW_LOCATOR = os.path.join(DATA_DIR, "raw_locator.json")
RAW_CHAMPS = os.path.join(DATA_DIR, "raw_championships.json")
EVENTS_JSON = os.path.join(DATA_DIR, "events.json")


def _reason(exc):
    """A one-line description of a fetch failure, without the stack."""
    if isinstance(exc, BotChallenge):
        return "pokemon.com bot protection"
    if isinstance(exc, AppRejected):
        return "the locator refused the request (%s)" % exc
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _fetch_help(exc):
    """The message shown when a fetch fails and there is nothing cached."""
    if isinstance(exc, BotChallenge):
        return (
            "\nCould not reach the Event Locator: pokemon.com served its\n"
            "bot-protection page ('Pardon Our Interruption') instead of data.\n\n"
            "This is rate-based and temporary - nothing is wrong with the script\n"
            "or your setup. It clears on its own, usually within 15-30 minutes.\n"
            "Scripted clients get flagged for a long time once this starts,\n"
            "so waiting may not be enough. Your own browser is unaffected:\n\n"
            "  1. Open https://events.pokemon.com/EventLocator/\n"
            "  2. Paste tools/fetch_in_browser.js into the developer console\n"
            "  3. python3 update.py --import-json\n\n"
            "'python3 update.py --offline' rebuilds from whatever is in data/."
        )
    if isinstance(exc, AppRejected):
        return (
            "\nThe Event Locator refused the request: %s\n\n"
            "The query reached Pokemon's servers, but the app session it\n"
            "wants could not be set up - the bootstrap call it relies on is\n"
            "being rate-limited. It is the same throttle as the bot-protection\n"
            "page, seen from the other side, and it clears on its own.\n\n"
            "Wait and try again later. If you need the data now, your own\n"
            "browser session still works:\n\n"
            "  1. Open https://events.pokemon.com/EventLocator/\n"
            "  2. Paste tools/fetch_in_browser.js into the developer console\n"
            "  3. python3 update.py --import-json"
            % exc
        )
    return (
        "\nCould not reach the Event Locator: %s\n\n"
        "If this persists, check your connection, then try again. Once a\n"
        "successful fetch has been cached, later failures fall back to it\n"
        "instead of stopping." % _reason(exc)
    )


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        sys.exit(
            "\nNo config file at %s\n\n"
            "config.json sits next to update.py and holds the place to search\n"
            "around. If it has been moved or deleted, restore it or point at\n"
            "another with --config." % path
        )
    except ValueError as exc:
        sys.exit("\n%s is not valid JSON: %s" % (path, exc))

    missing = [k for k in ("center", "radius_miles", "title") if k not in config]
    if missing:
        sys.exit(
            "\n%s is missing: %s\n"
            "See the Configuration section of README.md for the expected shape."
            % (path, ", ".join(missing))
        )
    for key in ("latitude", "longitude", "label"):
        if key not in config.get("center", {}):
            sys.exit("\n%s: center is missing %r" % (path, key))
    return config


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


AUTO_IMPORT = "<downloads>"


def find_downloaded_export():
    """Locate the newest ptcg-locator*.json the browser snippet saved."""
    downloads = os.path.expanduser("~/Downloads")
    candidates = []
    if os.path.isdir(downloads):
        for name in os.listdir(downloads):
            if name.startswith("ptcg-locator") and name.endswith(".json"):
                full = os.path.join(downloads, name)
                candidates.append((os.path.getmtime(full), full))
    if not candidates:
        sys.exit(
            "\nNo ptcg-locator*.json found in ~/Downloads.\n\n"
            "Run tools/fetch_in_browser.js in your browser console first, or\n"
            "pass the file explicitly:\n"
            "  python3 update.py --import-json /path/to/file.json"
        )
    return max(candidates)[1]


def import_locator(path):
    """Adopt a locator response saved from a browser session.

    Accepts either the whole response envelope or just the event list, so
    it does not matter which one gets saved.
    """
    payload = load_json(path)
    if payload is None:
        sys.exit("Could not read %s - is it valid JSON?" % path)

    records = payload
    if isinstance(payload, dict):
        records = (
            payload.get("data", {}).get("EventList", {}).get("List")
            or payload.get("EventList", {}).get("List")
            or payload.get("List")
        )
    if not isinstance(records, list):
        sys.exit(
            "%s does not look like a locator response.\n"
            "Expected a list of events, or the whole response with\n"
            "data.EventList.List inside it." % path
        )

    save_json(RAW_LOCATOR, records)
    print("  locator: imported %d events from %s" % (len(records), path))
    return records


def fetch_locator(config, radius, offline):
    """Locator events, falling back to the cached copy if the fetch fails."""
    if offline:
        cached = load_json(RAW_LOCATOR)
        if cached is None:
            print("  locator: nothing cached yet - run without --offline first")
            return None
        print("  locator: using cached copy (%d records)" % len(cached))
        return cached

    start = date.today()
    end = start + timedelta(days=config.get("days_ahead", 180))
    print("  locator: %s -> %s within %d mi of %s"
          % (start, end, radius, config["center"]["label"]))
    try:
        records = locator.fetch_events(
            config["center"]["latitude"],
            config["center"]["longitude"],
            radius,
            start.isoformat(),
            end.isoformat(),
            token_cache=os.path.join(DATA_DIR, "tokens.json"),
        )
        save_json(RAW_LOCATOR, records)
        print("  locator: %d events" % len(records))
        return records
    except Exception as exc:
        cached = load_json(RAW_LOCATOR)
        if cached is None:
            # One source being down should not cost you the whole calendar.
            print("  locator: unavailable (%s)" % _reason(exc))
            for line in _fetch_help(exc).strip().splitlines():
                print("    " + line if line else "")
            return None
        print("  locator: fetch failed (%s)" % _reason(exc))
        print("  locator: keeping the %d events from the last good fetch"
              % len(cached))
        return cached


def fetch_championships(offline):
    if offline:
        return load_json(RAW_CHAMPS, [])
    try:
        records = championships.fetch_events()
        save_json(RAW_CHAMPS, records)
        print("  championships: %d events" % len(records))
        return records
    except Exception as exc:
        cached = load_json(RAW_CHAMPS, [])
        print("  championships: fetch failed (%s); keeping %d cached events"
              % (_reason(exc), len(cached)))
        return cached


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    parser.add_argument("--radius", type=float, help="override search radius in miles")
    parser.add_argument("--offline", action="store_true",
                        help="rebuild the page from data/ without fetching")
    parser.add_argument("--open", dest="open_after", action="store_true",
                        help="open the calendar when it is built")
    parser.add_argument("--import-json", dest="import_json", metavar="FILE",
                        nargs="?", const=AUTO_IMPORT,
                        help="use a locator response saved from a browser "
                             "session (see tools/fetch_in_browser.js); with no "
                             "FILE, picks the newest ptcg-locator*.json in "
                             "~/Downloads")
    args = parser.parse_args()

    config = load_config(args.config)
    radius = args.radius or config["radius_miles"]
    lat = config["center"]["latitude"]
    lon = config["center"]["longitude"]

    print("Updating %s" % config["title"])
    if args.import_json:
        source = args.import_json
        if source == AUTO_IMPORT:
            source = find_downloaded_export()
        raw_locator = import_locator(source)
    else:
        raw_locator = fetch_locator(config, radius, args.offline)
    raw_champs = (
        fetch_championships(args.offline)
        if config.get("include_championships", True)
        else []
    )


    locator_down = raw_locator is None
    events = normalize.from_locator(raw_locator or [], lat, lon)

    geocoder = None
    if raw_champs:
        geocoder = Geocoder(
            os.path.join(DATA_DIR, "geocode_cache.json"),
            enabled=config.get("geocode_championships", True) and not args.offline,
        )
        events += normalize.from_championships(raw_champs, lat, lon, geocoder)
        geocoder.save()

    # Two locator rows can describe the same event; keep one of each.
    seen = set()
    deduped = []
    for event in events:
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        deduped.append(event)

    save_json(EVENTS_JSON, deduped)

    counts = {
        "locator": sum(1 for e in deduped if e["source"] == "locator"),
        "championships": sum(1 for e in deduped if e["source"] == "championships"),
    }
    notices = []
    if locator_down and args.offline:
        notices.append(
            "No local store events yet. This page was built offline; run "
            "'python3 update.py' with a connection to fetch them."
        )
    elif locator_down:
        notices.append(
            "Local store events could not be loaded - the Pokemon Event "
            "Locator's event feed did not respond. Championship events below "
            "are current. Run update.py again later to fill these in."
        )
    meta = build.build_meta(dict(config, radius_miles=radius), counts, notices)
    out_path = os.path.join(ROOT, config.get("output", "calendar.html"))
    build.render(deduped, meta, out_path, config["title"], radius)

    print("Built %s - %d local events, %d championship events"
          % (os.path.relpath(out_path, ROOT), counts["locator"], counts["championships"]))
    if locator_down:
        print("  (no local events: run 'python3 update.py' with a connection)"
              if args.offline else
              "  (local events are missing; re-run when the locator recovers)")

    if args.open_after:
        subprocess.run(["open", out_path], check=False)


if __name__ == "__main__":
    main()
