"""Render the self-contained calendar page.

The event data is inlined into the HTML rather than loaded from a side
file: browsers block fetch() from a file:// page, so a single document is
what makes double-clicking calendar.html work with no server.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

TEMPLATE = os.path.join(os.path.dirname(__file__), "template.html")


def render(events, meta, out_path, title, max_radius):
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()

    ordered = sorted(events, key=lambda e: (e["date"], e.get("time") or "", e["name"]))

    # </script> inside any event text would end the data block early.
    payload = json.dumps(ordered, ensure_ascii=False).replace("</", "<\\/")

    html = (
        html.replace("__EVENT_DATA__", payload)
        .replace("__META__", json.dumps(meta, ensure_ascii=False))
        .replace("__PAGE_TITLE__", title)
        .replace("__MAX_RADIUS__", str(int(max_radius)))
    )

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def build_meta(config, counts, notices=None):
    now = datetime.now()
    return {
        "center_label": config["center"]["label"],
        "radius_miles": config["radius_miles"],
        "updated": now.isoformat(timespec="seconds"),
        # %-I is not portable, so the hour is trimmed by hand.
        "updated_human": "%s at %d:%02d %s"
        % (
            now.strftime("%b %d, %Y"),
            now.hour % 12 or 12,
            now.minute,
            "AM" if now.hour < 12 else "PM",
        ),
        "counts": counts,
        "notices": notices or [],
    }
