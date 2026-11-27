"""Site clicks per pin from GoatCounter (free, no cookies).

Every pin links to its recipe with ?pin=<pin_id>; the site counts that visit as the
GoatCounter event "pin-<pin_id>". This reads those counts back so the weekly
learning loop can rank pins by real visits even without Pinterest's API.

Needs config site.goatcounter (the site code) and the GOATCOUNTER_TOKEN secret
(GoatCounter > your name > API > new token with permission to read statistics).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

from common import log


def pin_clicks(cfg: dict, since: datetime, until: datetime | None = None) -> dict[str, int]:
    code = cfg["site"].get("goatcounter")
    token = os.environ.get("GOATCOUNTER_TOKEN")
    if not code or not token:
        raise RuntimeError("GoatCounter not set up (site.goatcounter / GOATCOUNTER_TOKEN)")
    until = until or datetime.now(timezone.utc)
    url = f"https://{code}.goatcounter.com/api/v0/stats/hits"
    head = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fmt = "%Y-%m-%dT%H:00:00Z"                       # the API wants hour-rounded times
    out: dict[str, int] = {}
    seen: list[str] = []
    for _ in range(100):                             # pages of 100 paths
        params = [("start", since.strftime(fmt)), ("end", (until + timedelta(hours=1)).strftime(fmt)),
                  ("limit", "100")] + [("exclude_paths", pid) for pid in seen]
        r = requests.get(url, headers=head, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        data = r.json()
        for h in data.get("hits", []):
            seen.append(str(h["path_id"]))
            path = h.get("path", "")
            if h.get("event") and path.startswith("pin-"):
                out[path[4:]] = out.get(path[4:], 0) + int(h.get("count", 0))
        if not data.get("more"):
            break
        time.sleep(0.3)                              # API allows 4 requests a second
    log(f"goatcounter: clicks for {len(out)} pins")
    return out


if __name__ == "__main__":                           # quick check: python src/clicks.py
    from common import load_config
    cfg = load_config()
    base = f"https://{cfg['site']['goatcounter']}.goatcounter.com/api/v0"
    head = {"Authorization": f"Bearer {os.environ['GOATCOUNTER_TOKEN']}", "Content-Type": "application/json"}
    now = datetime.now(timezone.utc)
    for path, params in [("/me", {}), ("/sites", {}), ("/paths", {}), ("/stats/total", {}),
                         ("/stats/hits", {}), ("/stats/hits", {"start": (now - timedelta(days=7)).strftime("%Y-%m-%d")}),
                         ("/stats/hits", {"start": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:00:00Z")})]:
        r = requests.get(base + path, headers=head, params=params, timeout=30)
        body = r.text if "json" in r.headers.get("content-type", "") else r.text[:80]
        print(path, params, r.status_code, body[:400].replace(os.environ["GOATCOUNTER_TOKEN"], "***"))
    print(pin_clicks(cfg, now - timedelta(days=7)))
