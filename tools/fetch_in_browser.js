/* Fetch the Charlotte event list using your own browser session.
 *
 * The locator's data endpoints are behind a bot check that only a real
 * browser satisfies, so `python3 update.py` is often refused. Your own
 * browser session is not, and this borrows it.
 *
 *   1. Open https://events.pokemon.com/EventLocator/ and let it load.
 *   2. Open the developer console (Option-Command-J in Chrome,
 *      Option-Command-C in Safari with the Develop menu enabled).
 *   3. Paste this whole file in and press Return. The first time, Chrome
 *      asks you to type "allow pasting" before it will accept it.
 *   4. It downloads ptcg-locator.json.
 *   5. Back in the project: python3 update.py --import-json
 *
 * Adjust CENTRE below if you want a different area.
 */
(async () => {
  const CENTRE = { latitude: "35.2271", longitude: "-80.8431" }; // Charlotte, NC
  const RADIUS_MILES = 50;
  const DAYS_AHEAD = 180;

  const iso = (d) => d.toISOString().slice(0, 10);
  const today = new Date();
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + DAYS_AHEAD);

  const base = location.origin + "/EventLocator/";
  const { versionToken } = await (await fetch(base + "moduleservices/moduleversioninfo")).json();

  const payload = {
    versionInfo: { moduleVersion: versionToken, apiVersion: "lbKy3fbhrLZ5eb1JFcZNZw" },
    viewName: "MainFlow.Home",
    screenData: {
      variables: {
        latitude: CENTRE.latitude, longitude: CENTRE.longitude,
        range: String(RADIUS_MILES), iskm: false, locale: "en-US", filters: "",
        SortDistance: true, MaxRecords: 2000, Count: 0,
      },
    },
    clientVariables: {
      Latitude: CENTRE.latitude, Longitude: CENTRE.longitude, Range: RADIUS_MILES,
      IsKm: false, StartDate: iso(today), EndDate: iso(end), UserLocale: "en-US",
      IsShowEvents: true, IsSearch: true, FilterEventTypes: "", PremierEventsIdList: "",
      MaxDateLimit: 180, CurrTab: 0, IsChampionshipSeries: false, IsShowMap: false,
      AreEventsBeingSearched: false, IsOnReadyFinished: true, LocationName: "",
    },
  };

  /* An empty X-CSRFToken is rejected as "Invalid Login" once a real
     session exists, so try omitting it, then the token the app stores. */
  const stored = (() => {
    try {
      for (const key of Object.keys(window.localStorage)) {
        if (/csrf/i.test(key)) return window.localStorage.getItem(key);
      }
    } catch (err) { /* blocked */ }
    const match = document.cookie.match(/(?:^|;\s*)([^=]*csrf[^=]*)=([^;]+)/i);
    return match ? decodeURIComponent(match[2]) : null;
  })();

  const attempts = [
    ["no CSRF header", { "Content-Type": "application/json; charset=UTF-8" }],
  ];
  if (stored) {
    attempts.push(["stored CSRF token", {
      "Content-Type": "application/json; charset=UTF-8", "X-CSRFToken": stored,
    }]);
  }

  let data = null;
  for (const [label, headers] of attempts) {
    const response = await fetch(
      base + "screenservices/EventLocator/MainFlow/Home/DataActionGetEventList",
      { method: "POST", headers, body: JSON.stringify(payload), credentials: "same-origin" }
    );
    if (response.ok) {
      console.log("Worked with: " + label);
      data = await response.json();
      break;
    }
    console.warn("Refused (" + label + "): " + response.status + " " +
                 (await response.text()).slice(0, 160));
  }
  if (!data) {
    console.error("Every variant was refused. Use the Network tab instead:");
    console.error("  1. Switch the page to the Events view and search.");
    console.error("  2. Network tab -> filter for DataActionGetEventList");
    console.error("  3. Right-click it -> Copy -> Copy response");
    console.error("  4. Save as ptcg-locator.json in ~/Downloads");
    return;
  }

  const events = data?.data?.EventList?.List ?? [];

  const places = new Set(events.map((e) => e?.Events?.Address?.Name).filter(Boolean));
  console.log("Got " + events.length + " events across " + places.size + " venues.");
  if (events.length) {
    const days = events.map((e) => (e?.Events?.Start_date || "").slice(0, 10)).sort();
    console.log("Dates run " + days[0] + " to " + days[days.length - 1] + ".");
  }

  // Kept on the window so you can recover the data if the download is blocked.
  window.__ptcgEvents = events;

  try {
    const blob = new Blob([JSON.stringify(events)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ptcg-locator.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    console.log("Saved ptcg-locator.json - now run:  python3 update.py --import-json");
  } catch (err) {
    console.warn("Download blocked (" + err + ").");
    console.warn("Run this instead, then paste into a file:");
    console.warn("  copy(JSON.stringify(window.__ptcgEvents))");
  }
})();
