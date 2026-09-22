"""Minimal, polite HTTP helper.

Both pokemon.com properties sit behind Imperva bot protection. It lets a
small number of plain browser-shaped requests through but starts serving
a "Pardon Our Interruption" interstitial once a client looks automated,
so this module does three things:

* keeps a cookie jar and warms it with a normal page load first, the way
  a browser would arrive at the app before its XHR fires;
* sends exactly the headers a browser sends and no more - an extra
  ``X-Requested-With`` or ``Accept-Encoding`` is enough to stand out;
* recognises the interstitial and raises :class:`BotChallenge` instead of
  letting it surface as a confusing JSON parse error.
"""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

BROWSER_HINTS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

DOCUMENT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class BotChallenge(RuntimeError):
    """Imperva served a challenge page instead of the data.

    This is rate-based and clears on its own; it is not a permanent block
    and not a sign the request was malformed.
    """


class AppRejected(RuntimeError):
    """The request reached OutSystems, which refused it.

    In practice this means "Invalid Login": the app wants a session that
    is established by its own bootstrap call. When that bootstrap is the
    thing Imperva is blocking, the refusal shows up here instead, so it
    is a downstream symptom of the same rate limit rather than a separate
    fault.
    """


def _looks_like_challenge(body: str) -> bool:
    head = body[:3000].lower()
    return (
        "pardon our interruption" in head
        or "incapsula incident id" in head
        or "_incapsula_resource" in head
        or "request unsuccessful" in head
    )


def _app_error(body: str):
    """Pull an OutSystems exception message out of an error response."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    exception = payload.get("exception") if isinstance(payload, dict) else None
    if isinstance(exception, dict):
        return exception.get("message") or exception.get("name")
    return None


class Session:
    """A cookie-keeping opener, so repeat calls look like one visitor."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self._warmed = set()

    def request(self, url, data=None, headers=None, timeout=120, attempts=3,
                pause=5.0, challenge_retries=1):
        """Fetch a URL, retrying transient failures.

        A bot challenge is handled differently from a network error. It is
        rate-based, so retrying hard makes it worse and keeps it going for
        longer; one patient retry is allowed and then it gives up, which
        leaves the caller to fall back to cached data.
        """
        hdrs = dict(BROWSER_HINTS)
        if headers:
            hdrs.update(headers)

        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json; charset=UTF-8")

        last_error = None
        network_tries = 0
        challenge_tries = 0
        while True:
            req = urllib.request.Request(url, data=payload, headers=hdrs)
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                if _looks_like_challenge(body):
                    raise BotChallenge(
                        "pokemon.com served its bot-protection page instead of data"
                    )
                return body
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if _looks_like_challenge(detail):
                    last_error = BotChallenge(
                        "pokemon.com served its bot-protection page instead of data"
                    )
                    challenge_tries += 1
                    if challenge_tries > challenge_retries:
                        raise last_error
                    time.sleep(20.0)
                    continue
                message = _app_error(detail)
                if message:
                    # An application-level refusal will not change by retrying.
                    raise AppRejected(message)
                last_error = exc
                network_tries += 1
                if network_tries >= attempts:
                    raise
                time.sleep(pause * network_tries)
            except BotChallenge as exc:
                last_error = exc
                challenge_tries += 1
                if challenge_tries > challenge_retries:
                    raise
                time.sleep(20.0)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                network_tries += 1
                if network_tries >= attempts:
                    raise last_error
                time.sleep(pause * network_tries)

    def warm(self, page_url):
        """Load a page normally once, to pick up the session cookies."""
        if page_url in self._warmed:
            return
        try:
            self.request(page_url, headers=DOCUMENT_HEADERS, attempts=1,
                         challenge_retries=0)
        except Exception:
            # The warm-up is a courtesy; the real call reports any failure.
            pass
        self._warmed.add(page_url)

    def get_json(self, url, data=None, headers=None, **kwargs):
        body = self.request(url, data=data, headers=headers, **kwargs)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "expected JSON from %s but got %r" % (url, body[:200])
            ) from exc


_default = Session()


def request(url, **kwargs):
    return _default.request(url, **kwargs)


def get_json(url, **kwargs):
    return _default.get_json(url, **kwargs)
