# Play! Pokémon Event Calendar

A local, offline-first calendar of Play! Pokémon events near you. The
official Event Locator makes you re-run a search every time and shows a
list, not a calendar; this turns it into one, and folds in the
Championship Series that lives on a different site entirely.

- **[Play! Pokémon Event Locator](https://events.pokemon.com/EventLocator/)** —
  leagues, challenges, cups and prereleases at stores near you.
- **[Championship Series](https://championships.pokemon.com/en-us/events)** —
  Regionals, Specials and Internationals for the current season.

Everything lands in a single self-contained `calendar.html` you can
double-click. No server, no build step, no account, no dependencies.

It ships pointed at Charlotte, NC; one edit to `config.json` moves it
anywhere. A real run there returns around 350 events across 36 stores.

## Use it

```sh
python3 update.py            # fetch both sources and rebuild the page
open calendar.html
```

Python 3.9 or newer, standard library only — nothing to install.

### Options

| Command | What it does |
| --- | --- |
| `python3 update.py` | Refresh everything and rebuild |
| `python3 update.py --open` | Rebuild, then open the calendar |
| `python3 update.py --radius 75` | Widen the search for one run |
| `python3 update.py --offline` | Rebuild the page from `data/` without fetching |
| `python3 update.py --import-json` | Use the newest browser export from `~/Downloads` |
| `python3 test_ptcg.py` | Run the test suite (66 tests, no network) |

Run it again whenever you want to check for new events — stores sanction
new ones constantly, usually a few weeks out.

### If the locator refuses

Usually it just works. But the locator's event feed does go down — during
development it returned `403 Invalid Login` for several hours, to the
official site as well as to this script, while store search kept working.
When that happens the calendar still builds from cached and championship
data, and says so in a banner.

Rate limiting is the other cause, and that one is self-inflicted: run the
script in a loop and Imperva will start serving a bot-protection page to
scripted clients from your address for hours. One run makes three
requests, which is fine. Don't loop it.

If you need data while the script is locked out but the site itself works
in your browser, borrow that session:

1. Open <https://events.pokemon.com/EventLocator/> and let it finish loading.
2. Open the developer console: **Option-Command-J** in Chrome, or
   **Option-Command-C** in Safari with the Develop menu enabled.
3. Paste in the whole of [`tools/fetch_in_browser.js`](tools/fetch_in_browser.js)
   and press Return. The first time, Chrome makes you type `allow pasting`
   before it will accept a paste.
4. It downloads `ptcg-locator.json`. Back in the project:

   ```sh
   python3 update.py --import-json
   ```

With no filename it picks the newest `ptcg-locator*.json` out of
`~/Downloads`, so repeat runs are just those two steps. The import takes
either the bare event list or the whole response, so it does not matter
which you save.

Championship events are fetched normally either way — that is a different
host and it is not affected.

## In the page

- **Month** and **List** views, with today highlighted.
- Filters for game (TCG / VG / GO / UNITE), event type, store, distance
  and free text. They persist between visits.
- Click any event for the full detail: address, admission, contact,
  registration window and the organizer's own notes.
- **Add to Google Calendar** and **Directions** links per event, and
  **Export .ics** to drop every event currently showing into Apple
  Calendar, Google Calendar or Outlook in one go.
- Championship Series events are shown in purple and span their full
  weekend. Filter them by region or switch them off entirely.

## Configuration

`config.json` controls the search:

```json
{
  "center": { "label": "Charlotte, NC", "latitude": 35.2271, "longitude": -80.8431 },
  "radius_miles": 50,
  "days_ahead": 180
}
```

`title` is what the page calls itself, so the shipped config produces a
"Charlotte Pokémon Event Calendar". From uptown Charlotte, 50 miles
reaches Gastonia, Concord, Rock Hill, Mooresville, Salisbury and Hickory.

To point it somewhere else, put that place's latitude and longitude in
`center`, give it a `label` and `title`, and re-run. Right-click a spot in
Google Maps to copy its coordinates.

The distance slider in the page narrows results further without
refetching, so fetch wide and filter in the UI rather than re-running with
a small radius.

## How it works

| File | Role |
| --- | --- |
| `update.py` | Entry point: fetch, normalize, render |
| `ptcg/locator.py` | Talks to the Event Locator's OutSystems screen service |
| `ptcg/championships.py` | Reads the Championship Series feed, resolves its display dates |
| `ptcg/geocode.py` | Caches city coordinates so championships get a distance |
| `ptcg/normalize.py` | Folds both sources into one event shape |
| `ptcg/build.py` | Inlines the data into `ptcg/template.html` |
| `data/` | Raw responses, normalized events, caches |
| `test_ptcg.py` | Tests for all of the above |
| `tests/dom_shim.js` | Fake DOM, so the page can be run headlessly |
| `tools/fetch_in_browser.js` | Console snippet for the browser-session fallback |

## Tests

```sh
python3 test_ptcg.py
```

66 tests, none of which touch the network — the fetch paths run against
stubs. They cover the championship date parsing (including the season-year
rollover), the locator's misleading timestamps, address and distance
handling, HTML escaping and URL sanitising in the generated page, the
bot-challenge retry policy, and the failure messages.

The page's own JavaScript is not just parsed, it is **run**: `osascript`
supplies a JavaScript engine and `tests/dom_shim.js` a fake DOM, so the
tests render the month grid, switch views, apply every filter, open the
detail drawer and export `.ics` — the same code paths the browser takes.
That includes a walk across the November daylight-saving change, which is
where an earlier version of the grid duplicated Nov 1.

### Things worth knowing

- **Neither source has a public API.** The locator's endpoint and its two
  version tokens were read out of the app's own JavaScript; the tokens are
  re-discovered automatically if Pokémon redeploys the app. A layout change
  on their side could still break the fetch — the raw responses in `data/`
  mean the page keeps working while that gets fixed.
- **Two different refusals, and the script names which one it hit.**

  `403 Invalid Login` is misleading: nothing is wrong with your login and
  there is nothing to log into. It means the event feed itself is failing.
  During development it did this for hours, and Pokémon's own site showed
  the same thing — stores listed fine, events came back 403. Nothing to do
  but wait.

  A **"Pardon Our Interruption"** page is Imperva's bot protection, and it
  is caused by hammering. Once a scripted client is flagged, static files
  and the championships API keep working while `moduleservices/*` and the
  event query are refused, and it can persist for hours. Retrying extends
  it. Getting past it deliberately would mean executing Imperva's
  JavaScript challenge, which is the control it exists to enforce, so this
  project does not try.

  In both cases the calendar still builds from what it has.
- **Times are venue-local.** The locator stamps event times with a `Z` that
  is not real — a `18:30:00Z` event starts at 6:30 PM at the store, so the
  marker is stripped rather than converted.
- **The season year is not the calendar year.** Championship events are
  filed under the season they belong to, so the "2027" season starts in
  autumn 2026; dates are resolved accordingly.
- **The locator only knows what has been sanctioned.** Most stores are
  sanctioned a few weeks to a few months out, so the far end of the
  calendar thins out. That is the data, not a bug.

- **Most events have no name.** Weekly League sessions are published with
  an empty `Name` — 282 of the first 351 events pulled for Charlotte. The
  calendar shows the venue for those instead of an empty chip.

- **Pokémon GO is `pgo`, not `go`.** Both are accepted and folded
  together, so the colour and the game filter agree.

- **The same shop can appear twice** under slightly different spellings
  ("Mighty Meeple" and "The Mighty Meeple", "Cosmic Hearth Games" and
  "Cosmic Hearth Games LLC"). That is how the shops registered them; they
  are left as they came rather than guessed at.

Always confirm with the store before travelling.

## Fair use

This is a personal tool. Neither site publishes an API, so the endpoints
here were read out of the Event Locator's own JavaScript and the
championships page's own data feed; both can change or disappear without
warning, and one of them did mid-development.

Be a good citizen with it:

- One run makes three requests. Leave it that way — don't loop it, don't
  schedule it every five minutes. Hammering gets scripted clients blocked
  for hours, and the block is shared by anyone else on your address.
- The bundled geocode cache means normal use makes no geocoding requests
  at all. If you clear it, OpenStreetMap's Nominatim is queried once per
  city at one request per second, per their usage policy.
- Event data belongs to The Pokémon Company and the stores that list it.
  Keep it to yourself; don't republish it or build a service on it.

Not affiliated with, endorsed by, or connected to The Pokémon Company,
Nintendo, or Creatures Inc. Pokémon and Play! Pokémon are their
trademarks. This project claims no rights in them.

## Licence

MIT — see [LICENSE](LICENSE).
