#!/usr/bin/env python3
"""Tests for the calendar builder.

    python3 test_ptcg.py

Nothing here touches the network: the fetch paths are exercised with
stubs, so this is safe to run as often as you like.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ptcg import build, championships, http, normalize
from ptcg.geocode import Geocoder, haversine_miles

CHARLOTTE = (35.2271, -80.8431)

LOCATOR_SAMPLE = {
    "Events": {
        "Guid": "008b3b7b",
        "Activity_type": "tournament",
        "Subtype": "",
        "Name": "Weekly Play at the Mighty Meeple",
        "Display_id": "26-09-007167",
        "Products": {"List": ["tcg"]},
        "Category": "tcg_std",
        "Start_date": "2026-09-21T18:30:00Z",
        "Address": {
            "Name": "MIGHTY MEEPLE",
            "Full_address": "8440 PIT STOP CT NW 180, CONCORD, NC 28027, US",
            "Latitude": "35.375286",
            "Longitude": "-80.724853",
            "Timezone": "America/New_York",
        },
        "Event_website": "",
        "Registration_start": "2026-09-01T18:30:00+00:00",
        "Registration_end": "2026-09-21T18:30:00Z",
        "Details": "Weekly tournament",
        "Third_party_registration_website": "",
        "Contact_information": {"Email": "a@b.com", "Phone": "7046257913"},
        "Admission": "0",
        "Status": "sanctioned",
        "ActivityGroup": {"Display_name": "THE MIGHTY MEEPLE"},
    },
    "EventTypeName": "Friendly Tournament",
}


class ChampionshipDates(unittest.TestCase):
    """The feed ships display strings and a season year, not real dates."""

    def test_same_month_range(self):
        self.assertEqual(
            championships.parse_date_range("Sept. 18–20", 2027),
            ("2026-09-18", "2026-09-20"),
        )

    def test_range_crossing_months(self):
        self.assertEqual(
            championships.parse_date_range("Oct. 31 – Nov. 1", 2027),
            ("2026-10-31", "2026-11-01"),
        )

    def test_season_year_rolls_over_in_january(self):
        # The 2027 season starts in autumn 2026 and runs into 2027.
        self.assertEqual(
            championships.parse_date_range("Jan. 15–17", 2027),
            ("2027-01-15", "2027-01-17"),
        )
        self.assertEqual(
            championships.parse_date_range("Dec. 4–6", 2027),
            ("2026-12-04", "2026-12-06"),
        )

    def test_full_month_names(self):
        self.assertEqual(
            championships.parse_date_range("June 18–20", 2027),
            ("2027-06-18", "2027-06-20"),
        )
        self.assertEqual(
            championships.parse_date_range("May 8–9", 2027),
            ("2027-05-08", "2027-05-09"),
        )

    def test_single_day(self):
        self.assertEqual(
            championships.parse_date_range("Mar. 6", 2027), ("2027-03-06", "2027-03-06")
        )

    def test_plain_hyphen_and_em_dash(self):
        for dash in ("-", "—", "−"):
            self.assertEqual(
                championships.parse_date_range("Apr. 2%s4" % dash, 2027),
                ("2027-04-02", "2027-04-04"),
            )

    def test_year_wrapping_range(self):
        self.assertEqual(
            championships.parse_date_range("Dec. 31 - Jan. 1", 2027),
            ("2026-12-31", "2027-01-01"),
        )

    def test_unparseable_is_not_fatal(self):
        self.assertEqual(championships.parse_date_range("TBD", 2027), (None, None))
        self.assertEqual(championships.parse_date_range("", 2027), (None, None))


class LocatorNormalising(unittest.TestCase):
    def setUp(self):
        self.event = normalize.from_locator([LOCATOR_SAMPLE], *CHARLOTTE)[0]

    def test_trailing_z_is_stripped_not_converted(self):
        """18:30:00Z is 6:30 PM at the store, not 2:30 PM."""
        self.assertEqual(self.event["start"], "2026-09-21T18:30:00")
        self.assertEqual(self.event["time"], "18:30")
        self.assertEqual(self.event["date"], "2026-09-21")

    def test_offset_stamps_are_stripped_too(self):
        self.assertEqual(self.event["registration_start"], "2026-09-01T18:30:00")
        self.assertEqual(self.event["registration_end"], "2026-09-21T18:30:00")

    def test_city_and_state_from_address(self):
        self.assertEqual(self.event["city"], "Concord")
        self.assertEqual(self.event["state"], "NC")

    def test_shouted_names_are_readable(self):
        self.assertEqual(self.event["venue"], "Mighty Meeple")
        self.assertEqual(self.event["organizer"], "The Mighty Meeple")

    def test_acronyms_survive_title_casing(self):
        self.assertEqual(normalize._title("THE TCG SHOP LLC"), "The TCG Shop LLC")
        self.assertEqual(normalize._title("Already Nice"), "Already Nice")

    def test_zero_admission_reads_as_free(self):
        self.assertEqual(self.event["admission"], "Free")
        self.assertEqual(normalize._admission("$10"), "$10")
        self.assertEqual(normalize._admission(""), "")

    def test_distance_is_measured_from_the_configured_centre(self):
        self.assertAlmostEqual(self.event["distance_mi"], 12.2, delta=0.3)

    def test_nameless_league_events_get_a_usable_label(self):
        """Most weekly Leagues ship with an empty Name."""
        blank = json.loads(json.dumps(LOCATOR_SAMPLE))
        blank["Events"]["Name"] = ""
        blank["EventTypeName"] = "League"
        event = normalize.from_locator([blank], *CHARLOTTE)[0]
        self.assertEqual(event["name"], "League")
        self.assertTrue(event["generic"])
        self.assertEqual(event["venue"], "Mighty Meeple")

    def test_named_events_are_not_marked_generic(self):
        self.assertFalse(
            normalize.from_locator([LOCATOR_SAMPLE], *CHARLOTTE)[0]["generic"]
        )

    def test_pgo_is_recognised_as_pokemon_go(self):
        """The feed writes Pokemon GO as 'pgo', not 'go'."""
        record = json.loads(json.dumps(LOCATOR_SAMPLE))
        record["Events"]["Products"]["List"] = ["pgo", "tcg"]
        event = normalize.from_locator([record], *CHARLOTTE)[0]
        self.assertEqual(event["products"], ["pgo", "tcg"])
        self.assertIn("Pokémon GO", event["product_labels"])

    def test_go_is_folded_onto_pgo(self):
        record = json.loads(json.dumps(LOCATOR_SAMPLE))
        record["Events"]["Products"]["List"] = ["go"]
        event = normalize.from_locator([record], *CHARLOTTE)[0]
        self.assertEqual(event["products"], ["pgo"])

    def test_event_with_no_start_is_skipped(self):
        broken = json.loads(json.dumps(LOCATOR_SAMPLE))
        broken["Events"]["Start_date"] = ""
        self.assertEqual(normalize.from_locator([broken], *CHARLOTTE), [])

    def test_missing_coordinates_leave_distance_blank(self):
        broken = json.loads(json.dumps(LOCATOR_SAMPLE))
        broken["Events"]["Address"]["Latitude"] = ""
        event = normalize.from_locator([broken], *CHARLOTTE)[0]
        self.assertIsNone(event["distance_mi"])
        self.assertIsNone(event["lat"])

    def test_sparse_record_does_not_raise(self):
        sparse = {"Events": {"Guid": "x", "Start_date": "2026-10-01T12:00:00Z"}}
        event = normalize.from_locator([sparse], *CHARLOTTE)[0]
        self.assertEqual(event["date"], "2026-10-01")
        self.assertEqual(event["venue"], "")


class ChampionshipNormalising(unittest.TestCase):
    def setUp(self):
        self.record = {
            "name": "2027 Baltimore Pokémon Regional Championships",
            "start": "2026-09-18",
            "end": "2026-09-20",
            "display_range": "Sept. 18–20",
            "location": "Baltimore, MD",
            "type": "regional",
            "region": "northamerica",
            "season": 2027,
            "streaming": True,
            "url": "https://championships.pokemon.com/en-us/events/regionals/2027/baltimore",
        }

    def test_multi_day_and_all_day(self):
        event = normalize.from_championships([self.record], *CHARLOTTE)[0]
        self.assertTrue(event["all_day"])
        self.assertEqual(event["date"], "2026-09-18")
        self.assertEqual(event["end_date"], "2026-09-20")
        self.assertEqual(event["region"], "North America")
        self.assertEqual(event["type"], "Regional Championships")

    def test_specials_are_labelled_separately(self):
        special = dict(self.record, name="2027 Lisbon Pokémon Special Championships")
        event = normalize.from_championships([special], *CHARLOTTE)[0]
        self.assertEqual(event["type"], "Special Championships")

    def test_distance_uses_the_geocode_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.json")
            with open(path, "w") as fh:
                json.dump({"baltimore, md": [39.2904, -76.6122]}, fh)
            geocoder = Geocoder(path, enabled=False)
            event = normalize.from_championships(
                [self.record], *CHARLOTTE, geocoder=geocoder
            )[0]
        self.assertAlmostEqual(event["distance_mi"], 360, delta=15)

    def test_unknown_city_leaves_distance_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            geocoder = Geocoder(os.path.join(tmp, "cache.json"), enabled=False)
            event = normalize.from_championships(
                [self.record], *CHARLOTTE, geocoder=geocoder
            )[0]
        self.assertIsNone(event["distance_mi"])


class Distance(unittest.TestCase):
    def test_known_distance(self):
        # Charlotte -> Gastonia is about 21 miles.
        self.assertAlmostEqual(
            haversine_miles(35.2271, -80.8431, 35.2621, -81.1873), 19.8, delta=1.5
        )

    def test_zero(self):
        self.assertEqual(haversine_miles(35.2271, -80.8431, 35.2271, -80.8431), 0.0)


class PageBuilding(unittest.TestCase):
    def setUp(self):
        self.events = normalize.from_locator([LOCATOR_SAMPLE], *CHARLOTTE)
        self.meta = build.build_meta(
            {"center": {"label": "Charlotte, NC"}, "radius_miles": 50},
            {"locator": 1, "championships": 0},
        )
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, events=None):
        out = os.path.join(self.tmp, "calendar.html")
        build.render(events or self.events, self.meta, out, "Test Calendar", 50)
        with open(out, encoding="utf-8") as fh:
            return fh.read()

    def test_every_placeholder_is_replaced(self):
        html = self.render()
        for token in ("__EVENT_DATA__", "__META__", "__PAGE_TITLE__", "__MAX_RADIUS__"):
            self.assertNotIn(token, html)

    def test_closing_script_tag_in_event_text_cannot_break_out(self):
        nasty = json.loads(json.dumps(LOCATOR_SAMPLE))
        nasty["Events"]["Details"] = "</script><script>alert(1)</script>"
        html = self.render(normalize.from_locator([nasty], *CHARLOTTE))
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("<\\/script>", html)

    def test_data_block_is_valid_json_and_sorted(self):
        champ = normalize.from_championships(
            [
                {
                    "name": "Later Event",
                    "start": "2026-12-04",
                    "end": "2026-12-06",
                    "display_range": "Dec. 4-6",
                    "location": "Las Vegas, NV",
                    "type": "regional",
                    "region": "northamerica",
                    "season": 2027,
                    "url": "x",
                }
            ],
            *CHARLOTTE,
        )
        html = self.render(champ + self.events)
        payload = re.search(r"const DATA = (\[.*?\]);\n", html, re.S).group(1)
        rows = json.loads(payload.replace("<\\/", "</"))
        self.assertEqual([r["date"] for r in rows], ["2026-09-21", "2026-12-04"])

    def test_page_is_self_contained(self):
        """No external fetch, so file:// works with no server."""
        html = self.render()
        self.assertNotIn("fetch(", html)
        self.assertNotIn("<script src", html)

    def test_title_reaches_the_page(self):
        self.assertIn("<title>Test Calendar</title>", self.render())


class ChallengeDetection(unittest.TestCase):
    def test_interstitial_is_recognised(self):
        for body in (
            "<html><head><title>Pardon Our Interruption</title>",
            "Request unsuccessful. Incapsula incident ID: 12345",
            '<script src="/_Incapsula_Resource?SWJIYLWA=abc"></script>',
        ):
            self.assertTrue(http._looks_like_challenge(body), body[:40])

    def test_real_data_is_not_mistaken_for_a_challenge(self):
        self.assertFalse(http._looks_like_challenge('{"data":{"EventList":{"List":[]}}}'))


class RetryPolicy(unittest.TestCase):
    """A bot challenge must not be hammered; a network blip should retry."""

    class FakeResponse:
        def __init__(self, body):
            self.body = body.encode("utf-8")

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def session_over(self, bodies):
        """A session whose opener replays the given bodies/exceptions."""
        session = http.Session()
        calls = []

        class FakeOpener:
            def open(inner, req, timeout=None):
                calls.append(req.full_url)
                item = bodies[min(len(calls) - 1, len(bodies) - 1)]
                # A callable yields a fresh object per call, because an
                # HTTPError's body can only be read once.
                if callable(item):
                    item = item()
                if isinstance(item, Exception):
                    raise item
                return RetryPolicy.FakeResponse(item)

        session.opener = FakeOpener()
        return session, calls

    def test_challenge_is_tried_at_most_twice(self):
        challenge = "<title>Pardon Our Interruption</title>"
        session, calls = self.session_over([challenge])
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(http.BotChallenge):
                session.request("https://example.test/x")
        self.assertEqual(len(calls), 2, "challenge should not be retried repeatedly")

    def test_challenge_that_clears_on_retry_succeeds(self):
        session, calls = self.session_over(
            ["<title>Pardon Our Interruption</title>", '{"ok":true}']
        )
        with mock.patch("ptcg.http.time.sleep"):
            body = session.request("https://example.test/x")
        self.assertEqual(body, '{"ok":true}')
        self.assertEqual(len(calls), 2)

    def test_network_error_retries_more_patiently(self):
        session, calls = self.session_over([urllib.error.URLError("down")])
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(urllib.error.URLError):
                session.request("https://example.test/x", attempts=3)
        self.assertEqual(len(calls), 3)

    def test_warm_up_failure_is_swallowed(self):
        session, calls = self.session_over(["<title>Pardon Our Interruption</title>"])
        with mock.patch("ptcg.http.time.sleep"):
            session.warm("https://example.test/")  # must not raise
        self.assertEqual(len(calls), 1, "warm-up should not retry a challenge")

    def test_warm_up_runs_once_per_url(self):
        session, calls = self.session_over(["<html>fine</html>"])
        session.warm("https://example.test/")
        session.warm("https://example.test/")
        self.assertEqual(len(calls), 1)

    def test_cookies_are_kept_across_calls(self):
        self.assertIsInstance(http.Session().jar, http.http.cookiejar.CookieJar)


class AppRefusal(unittest.TestCase):
    """A 403 from OutSystems is a different animal from a bot challenge."""

    def make_http_error(self, body, code=403):
        return urllib.error.HTTPError(
            "https://example.test/x", code, "Forbidden", {}, io.BytesIO(body.encode())
        )

    def test_outsystems_exception_is_surfaced_verbatim(self):
        session, calls = RetryPolicy.session_over(
            self,
            [self.make_http_error(
                '{"data":{},"exception":{"name":"ServerException",'
                '"message":"Invalid Login"}}'
            )],
        )
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(http.AppRejected) as caught:
                session.request("https://example.test/x")
        self.assertEqual(str(caught.exception), "Invalid Login")

    def test_app_refusal_is_not_retried(self):
        session, calls = RetryPolicy.session_over(
            self,
            [self.make_http_error('{"exception":{"message":"Invalid Login"}}')],
        )
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(http.AppRejected):
                session.request("https://example.test/x")
        self.assertEqual(len(calls), 1, "an app refusal will not change on retry")

    def test_challenge_delivered_as_an_http_error_is_still_a_challenge(self):
        session, _ = RetryPolicy.session_over(
            self,
            [lambda: self.make_http_error("<title>Pardon Our Interruption</title>")],
        )
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(http.BotChallenge):
                session.request("https://example.test/x")

    def test_plain_http_error_still_retries(self):
        session, calls = RetryPolicy.session_over(
            self, [lambda: self.make_http_error("upstream boom", code=502)]
        )
        with mock.patch("ptcg.http.time.sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                session.request("https://example.test/x", attempts=2)
        self.assertEqual(len(calls), 2)

    def test_message_points_at_the_browser_route(self):
        import update

        message = update._fetch_help(http.AppRejected("Invalid Login"))
        self.assertIn("Invalid Login", message)
        self.assertIn("fetch_in_browser.js", message)
        self.assertIn("--import-json", message)
        self.assertNotIn("Traceback", message)


class BrowserImport(unittest.TestCase):
    """The escape hatch: adopt a response saved from a browser session."""

    def setUp(self):
        import update

        self.update = update
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.raw = os.path.join(self.tmp, "raw_locator.json")
        patcher = mock.patch.object(update, "RAW_LOCATOR", self.raw)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, payload):
        path = os.path.join(self.tmp, "in.json")
        with open(path, "w") as fh:
            json.dump(payload, fh)
        return path

    def test_accepts_a_bare_list(self):
        path = self.write([LOCATOR_SAMPLE])
        self.assertEqual(len(self.update.import_locator(path)), 1)
        self.assertTrue(os.path.exists(self.raw))

    def test_accepts_the_whole_response_envelope(self):
        path = self.write({"data": {"EventList": {"List": [LOCATOR_SAMPLE]}}})
        self.assertEqual(len(self.update.import_locator(path)), 1)

    def test_accepts_an_inner_fragment(self):
        path = self.write({"EventList": {"List": [LOCATOR_SAMPLE]}})
        self.assertEqual(len(self.update.import_locator(path)), 1)

    def test_rejects_something_else_with_a_clear_message(self):
        path = self.write({"nothing": "useful"})
        with self.assertRaises(SystemExit) as caught:
            self.update.import_locator(path)
        self.assertIn("does not look like a locator response", str(caught.exception))

    def test_auto_discovery_picks_the_newest_download(self):
        downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(downloads)
        older = os.path.join(downloads, "ptcg-locator.json")
        newer = os.path.join(downloads, "ptcg-locator (1).json")
        for path in (older, newer):
            with open(path, "w") as fh:
                json.dump([LOCATOR_SAMPLE], fh)
        os.utime(older, (1000, 1000))
        os.utime(newer, (2000, 2000))
        with mock.patch("os.path.expanduser", return_value=downloads):
            self.assertEqual(self.update.find_downloaded_export(), newer)

    def test_auto_discovery_ignores_unrelated_files(self):
        downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(downloads)
        with open(os.path.join(downloads, "something-else.json"), "w") as fh:
            fh.write("{}")
        with mock.patch("os.path.expanduser", return_value=downloads):
            with self.assertRaises(SystemExit) as caught:
                self.update.find_downloaded_export()
        self.assertIn("No ptcg-locator*.json", str(caught.exception))
        self.assertIn("fetch_in_browser.js", str(caught.exception))

    def test_missing_file_is_explained(self):
        with self.assertRaises(SystemExit) as caught:
            self.update.import_locator(os.path.join(self.tmp, "nope.json"))
        self.assertIn("Could not read", str(caught.exception))

    def test_imported_events_normalise_like_fetched_ones(self):
        path = self.write([LOCATOR_SAMPLE])
        events = normalize.from_locator(self.update.import_locator(path), *CHARLOTTE)
        self.assertEqual(events[0]["venue"], "Mighty Meeple")


class ConfigErrors(unittest.TestCase):
    """A broken config should explain itself, not print a stack trace."""

    def setUp(self):
        import update

        self.update = update
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_missing_file(self):
        with self.assertRaises(SystemExit) as caught:
            self.update.load_config(os.path.join(self.tmp, "nope.json"))
        self.assertIn("No config file at", str(caught.exception))
        self.assertIn("--config", str(caught.exception))

    def test_invalid_json(self):
        with self.assertRaises(SystemExit) as caught:
            self.update.load_config(self.write("{not json"))
        self.assertIn("not valid JSON", str(caught.exception))

    def test_missing_keys_are_named(self):
        with self.assertRaises(SystemExit) as caught:
            self.update.load_config(self.write('{"center": {}}'))
        message = str(caught.exception)
        self.assertIn("radius_miles", message)
        self.assertIn("title", message)

    def test_incomplete_centre_is_named(self):
        with self.assertRaises(SystemExit) as caught:
            self.update.load_config(
                self.write('{"center": {"latitude": 1}, "radius_miles": 50, "title": "x"}')
            )
        self.assertIn("center is missing", str(caught.exception))

    def test_the_shipped_config_is_valid(self):
        root = os.path.dirname(os.path.abspath(__file__))
        config = self.update.load_config(os.path.join(root, "config.json"))
        self.assertEqual(config["center"]["label"], "Charlotte, NC")
        self.assertGreater(config["radius_miles"], 0)


class BrowserSnippet(unittest.TestCase):
    def test_snippet_parses_and_targets_the_right_action(self):
        root = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(root, "tools", "fetch_in_browser.js")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        # It must agree with the Python client about the data action.
        from ptcg import locator

        self.assertIn(locator.FALLBACK_API_VERSION, source)
        self.assertIn("DataActionGetEventList", source)
        self.assertIn("moduleversioninfo", source)

        if not shutil.which("osascript"):
            self.skipTest("no JavaScript engine available")
        check = (
            'var s = $.NSString.stringWithContentsOfFileEncodingError('
            '"%s", $.NSUTF8StringEncoding, null).js;'
            'try { new Function(s); "PARSE OK" } catch (e) { "ERR: " + e.message }' % path
        )
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", check],
            capture_output=True, text=True,
        )
        self.assertIn("PARSE OK", result.stdout, result.stdout + result.stderr)


class FailureMessages(unittest.TestCase):
    """A blocked fetch should explain itself, never print a stack trace."""

    def setUp(self):
        import update

        self.update = update

    def test_bot_challenge_message_is_actionable(self):
        message = self.update._fetch_help(http.BotChallenge("blocked"))
        self.assertIn("bot-protection", message)
        self.assertIn("temporary", message)
        self.assertIn("--offline", message)
        self.assertNotIn("Traceback", message)

    def test_reason_is_a_single_line(self):
        self.assertEqual(
            self.update._reason(http.BotChallenge("x")), "pokemon.com bot protection"
        )
        self.assertEqual(len(self.update._reason(OSError("boom")).splitlines()), 1)


class EndToEnd(unittest.TestCase):
    """Run update.py against stubbed sources, with no network."""

    def test_build_from_cache_without_network(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.dirname(os.path.abspath(__file__))

        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir)
        with open(os.path.join(data_dir, "raw_locator.json"), "w") as fh:
            json.dump([LOCATOR_SAMPLE], fh)
        with open(os.path.join(data_dir, "raw_championships.json"), "w") as fh:
            json.dump([], fh)

        config = os.path.join(tmp, "config.json")
        with open(config, "w") as fh:
            json.dump(
                {
                    "center": {"label": "Charlotte, NC", "latitude": 35.2271,
                               "longitude": -80.8431},
                    "radius_miles": 50,
                    "days_ahead": 180,
                    "title": "Stub Calendar",
                    "output": os.path.join(tmp, "calendar.html"),
                },
                fh,
            )

        # update.py resolves data/ next to itself, so run a copy inside tmp.
        shutil.copy(os.path.join(root, "update.py"), tmp)
        shutil.copytree(os.path.join(root, "ptcg"), os.path.join(tmp, "ptcg"))

        result = subprocess.run(
            [sys.executable, "update.py", "--offline", "--config", config],
            cwd=tmp, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("1 local events", result.stdout)
        self.assertTrue(os.path.exists(os.path.join(tmp, "calendar.html")))


class PageScript(unittest.TestCase):
    """Exercise the page's own date maths in a real JS engine."""

    HARNESS = """
    var out = [];
    function check(label, got, want) {
      out.push((String(got) === String(want) ? "ok   " : "FAIL ") +
               label + " => " + got + (String(got) === String(want) ? "" : " (want " + want + ")"));
    }
    check("fmtTime 18:30", fmtTime("18:30"), "6:30 PM");
    check("fmtTime 12:00", fmtTime("12:00"), "12 PM");
    check("fmtTime 00:30", fmtTime("00:30"), "12:30 AM");
    check("key(parseDay)", key(parseDay("2026-09-21")), "2026-09-21");

    // Stepping across the end of US daylight saving must not repeat Nov 1.
    var d = parseDay("2026-10-30"), seq = [];
    for (var i = 0; i < 5; i++) { seq.push(key(d)); d = addDays(d, 1); }
    check("DST walk", seq.join(","), "2026-10-30,2026-10-31,2026-11-01,2026-11-02,2026-11-03");

    var champ = {id:"b", source:"championships", name:"Straddle", date:"2026-10-31",
                 end_date:"2026-11-01", time:"", all_day:true, products:[], type:"t",
                 address:"Baltimore, MD", details:"", website:"", admission:""};
    var local = {id:"a", source:"locator", name:"Weekly, Play", date:"2026-09-21",
                 end_date:"2026-09-21", time:"18:30", start:"2026-09-21T18:30:00",
                 all_day:false, products:["tcg"], address:"1 Main St",
                 type:"Friendly Tournament", admission:"Free", details:"x", website:""};

    check("multi-day spans both days",
          [...byDay([champ]).keys()].sort().join(","), "2026-10-31,2026-11-01");
    check("timed event indexed once", [...byDay([local]).keys()].join(","), "2026-09-21");

    check("gcal timed", decodeURIComponent(gcalLink(local).split("dates=")[1].split("&")[0]),
          "20260921T183000/20260921T213000");
    check("gcal all-day end is exclusive",
          decodeURIComponent(gcalLink(champ).split("dates=")[1].split("&")[0]),
          "20261031/20261102");

    var ics = toICS([local, champ]);
    check("ics timed start", /DTSTART:(\\d+T\\d+)/.exec(ics)[1], "20260921T183000");
    check("ics all-day end is exclusive", /DTEND;VALUE=DATE:(\\d+)/.exec(ics)[1], "20261102");
    check("ics escapes commas", /SUMMARY:(.*)/.exec(ics)[1].trim(), "Weekly\\\\, Play");
    check("ics is well formed",
          ics.indexOf("BEGIN:VCALENDAR") === 0 && /END:VCALENDAR$/.test(ics), "true");

    check("safeUrl keeps https", safeUrl("https://a.com/x"), "https://a.com/x");
    check("safeUrl upgrades bare domain", safeUrl("getsomegame.com"), "https://getsomegame.com");
    check("safeUrl rejects javascript:", safeUrl("javascript:alert(1)"), "");
    check("safeUrl rejects data:", safeUrl("data:text/html,<script>"), "");
    check("safeUrl rejects vbscript:", safeUrl("vbscript:msgbox"), "");
    check("safeUrl handles blank", safeUrl(""), "");
    check("safeUrl handles null", safeUrl(null), "");
    check("colour by product", colorClass(local), "tcg");
    check("colour for championships", colorClass(champ), "champ");
    check("escaping", esc('<b>&"x"</b>'), "&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;");
    out.join("\\n");
    """

    def test_page_helpers(self):
        if not shutil.which("osascript"):
            self.skipTest("no JavaScript engine available")

        template = os.path.join(os.path.dirname(build.TEMPLATE), "template.html")
        with open(template, encoding="utf-8") as fh:
            script = re.findall(r"<script>(.*?)</script>", fh.read(), re.S)[-1]

        wanted = ["parseDay", "addDays", "key", "fmtTime", "colorClass", "esc",
                  "safeUrl", "byDay", "toICS", "gcalLink"]
        pieces = []
        for name in wanted:
            found = re.search(r"\nfunction %s\(.*?\n\}\n" % name, script, re.S)
            self.assertIsNotNone(found, "helper %s not found in template" % name)
            pieces.append(found.group(0))

        shim = (
            "const MONTHS=[],DOW=[];\n"
            "function URLSearchParams(o){this.o=o;}\n"
            "URLSearchParams.prototype.toString=function(){var p=[];"
            "for(var k in this.o)p.push(k+'='+encodeURIComponent(this.o[k]));"
            "return p.join('&');};\n"
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(shim + "\n".join(pieces) + self.HARNESS)
            path = fh.name
        self.addCleanup(os.unlink, path)

        result = subprocess.run(
            ["osascript", "-l", "JavaScript", path], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        print("\n" + result.stdout.strip())
        self.assertNotIn("FAIL", result.stdout, result.stdout)

    def test_page_runs_against_a_dom(self):
        """Actually execute the page's code; parsing alone misses runtime bugs."""
        if not shutil.which("osascript"):
            self.skipTest("no JavaScript engine available")

        root = os.path.dirname(os.path.abspath(__file__))
        shim_path = os.path.join(root, "tests", "dom_shim.js")
        if not os.path.exists(shim_path):
            self.skipTest("dom shim missing")

        events = normalize.from_locator([LOCATOR_SAMPLE], *CHARLOTTE)
        events += normalize.from_championships(
            [
                {
                    "name": "2027 Atlanta Pokémon Regional Championships",
                    "start": "2027-01-15", "end": "2027-01-17",
                    "display_range": "Jan. 15-17", "location": "Atlanta, GA",
                    "type": "regional", "region": "northamerica", "season": 2027,
                    "url": "https://championships.pokemon.com/x",
                }
            ],
            *CHARLOTTE,
        )
        meta = build.build_meta(
            {"center": {"label": "Charlotte, NC"}, "radius_miles": 50},
            {"locator": 1, "championships": 1},
        )
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "c.html")
        build.render(events, meta, out, "Test", 50)
        with open(out, encoding="utf-8") as fh:
            script = re.findall(r"<script>(.*?)</script>", fh.read(), re.S)[-1]

        with open(shim_path, encoding="utf-8") as fh:
            shim = fh.read()

        harness = r"""
        var out = [];
        function check(label, got, want) {
          out.push((String(got) === String(want) ? "ok   " : "FAIL ") + label +
                   " => " + got + (String(got) === String(want) ? "" : " (want " + want + ")"));
        }
        function contains(label, hay, needle) {
          out.push((String(hay).indexOf(needle) !== -1 ? "ok   " : "FAIL ") +
                   label + (String(hay).indexOf(needle) !== -1 ? "" : " (missing " + needle + ")"));
        }

        check("all events loaded", EVENTS.length, 2);
        contains("month grid rendered", document.getElementById("view").innerHTML, "grid-head");
        contains("event chip rendered", document.getElementById("view").innerHTML, "Weekly Play");
        contains("stats rendered", document.getElementById("stats").innerHTML, "Next 7 days");
        check("count line", document.getElementById("shown").textContent, "2 of 2 events");

        // The list view must render without touching the month grid's state.
        state.view = "list"; state.cursor = parseDay("2026-09-01"); render();
        contains("list rendered", document.getElementById("view").innerHTML, "daygroup");

        // Championship weekends appear on every day they cover.
        state.cursor = parseDay("2027-01-01"); render();
        var listed = document.getElementById("view").innerHTML;
        /* One row per day of the weekend - count rows, not name mentions. */
        check("multi-day listed once per day",
              (listed.match(/class="row champ"/g) || []).length, 3);
        check("and grouped under three dates",
              (listed.match(/class="daygroup"/g) || []).length, 3);

        // Filters narrow the set.
        state.q = "mighty"; render();
        check("search filters", visible().length, 1);
        state.q = "";

        state.champOn = false; render();
        check("championships can be hidden", visible().length, 1);
        state.champOn = true;

        state.dist = 5; render();
        check("distance filter excludes Concord", visible().length, 1);
        state.dist = 50;

        state.products = new Set(["vg"]); render();
        check("product filter excludes tcg-only", visible().length, 1);
        state.products = new Set(["tcg", "vg", "pgo", "unite"]);

        // An empty result set must not blow up.
        state.q = "zzzz-no-such-event"; render();
        contains("empty state", document.getElementById("view").innerHTML, "No events match");
        state.q = ""; render();

        // The detail drawer builds for both kinds of event.
        openDetail(EVENTS[0]);
        openDetail(EVENTS[1]);
        var drawer = document.body.children[document.body.children.length - 1];
        contains("drawer has actions", drawer.innerHTML, "Add to Google Calendar");
        contains("drawer shows region", drawer.innerHTML, "North America");

        check("ics covers everything visible",
              (toICS(visible()).match(/BEGIN:VEVENT/g) || []).length, 2);

        // Saved filters survive a reload.
        state.q = "meeple"; saveState();
        state.q = ""; loadState();
        check("filters persist", state.q, "meeple");

        out.join("\n");
        """

        js = os.path.join(tmp, "run.js")
        with open(js, "w", encoding="utf-8") as fh:
            fh.write(shim + "\n" + script + "\n" + harness)

        result = subprocess.run(
            ["osascript", "-l", "JavaScript", js], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0,
                         "page threw at runtime:\n" + result.stderr)
        print("\n" + result.stdout.strip())
        self.assertNotIn("FAIL", result.stdout, result.stdout)

    def test_template_parses_as_javascript(self):
        if not shutil.which("osascript"):
            self.skipTest("no JavaScript engine available")
        events = normalize.from_locator([LOCATOR_SAMPLE], *CHARLOTTE)
        meta = build.build_meta(
            {"center": {"label": "Charlotte, NC"}, "radius_miles": 50}, {}
        )
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "c.html")
        build.render(events, meta, out, "T", 50)
        with open(out, encoding="utf-8") as fh:
            script = re.findall(r"<script>(.*?)</script>", fh.read(), re.S)[-1]
        js = os.path.join(tmp, "s.js")
        with open(js, "w", encoding="utf-8") as fh:
            fh.write(script)

        check = (
            'var s = $.NSString.stringWithContentsOfFileEncodingError('
            '"%s", $.NSUTF8StringEncoding, null).js;'
            'try { new Function(s); "PARSE OK" } catch (e) { "PARSE ERROR: " + e.message }'
            % js
        )
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", check],
            capture_output=True, text=True,
        )
        self.assertIn("PARSE OK", result.stdout, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
