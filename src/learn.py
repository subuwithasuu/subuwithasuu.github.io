"""Weekly feedback loop: what do our best pins have in common?

Method (adapted from the post-scorer skill, charlie947 MIT, using only OUR data):
  1. score every matured pin (posted >= 7 days ago) = saves + 2 x outbound clicks
     (outbound clicks count double: they are the visits to the site)
  2. compare the top 10 vs the bottom 10 by: hook type, layout, colour palette,
     board (recipe type), pin kind (image/video), protein band, time band
  3. turn the average score of every attribute value into a smoothed weight
     (1.0 = average, clipped to 0.5..2.0) -> data/state/learnings.json
Those weights then steer the queue (which pins/layouts/palettes get posted), the
board mix of new recipes, and the hook types Gemini is asked to write.
Nothing is claimed below MIN_PINS scored pins: small samples are noise.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta

from common import POSTED_FILE, STATE_DIR, load_config, now_utc, read_json, write_json

METRICS_FILE = STATE_DIR / "metrics.json"
LEARNINGS_FILE = STATE_DIR / "learnings.json"
ATTRS = ["hook_type", "layout", "palette", "board", "kind", "protein_band", "time_band"]
MIN_PINS = 20          # need at least top 10 + bottom 10
MATURE_DAYS = 7
PRIOR_N = 5            # smoothing: each value starts as if it had 5 average pins
CLIP = (0.5, 2.0)
LABEL = {"hook_type": "Hook type", "layout": "Layout", "palette": "Colour palette", "board": "Board / recipe type",
         "kind": "Pin type", "protein_band": "Protein", "time_band": "Total time"}


def score(m: dict) -> int:
    return int(m.get("SAVE", 0) or 0) + 2 * int(m.get("OUTBOUND_CLICK", 0) or 0)


def bands(p: dict) -> dict:
    pg, tm = p.get("protein_g"), p.get("total_minutes")
    out = {}
    if isinstance(pg, (int, float)):
        out["protein_band"] = "40g+" if pg >= 40 else "30-39g" if pg >= 30 else "20-29g" if pg >= 20 else "<20g"
    if isinstance(tm, (int, float)):
        out["time_band"] = "<=15 min" if tm <= 15 else "16-30 min" if tm <= 30 else ">30 min"
    return out


def analyze(posted: list[dict], metrics: dict, now: datetime) -> dict:
    rows = []
    for p in posted:
        m = metrics.get(p["pin_id"])
        if not m or now - datetime.fromisoformat(p["posted_at"]) < timedelta(days=MATURE_DAYS):
            continue
        row = {**p, **bands(p), "score": score(m), "saves": int(m.get("SAVE", 0) or 0),
               "clicks": int(m.get("OUTBOUND_CLICK", 0) or 0)}
        if row.get("kind") == "video":
            row["layout"] = "video"
        row["palette"] = str(row["palette"]) if row.get("palette") is not None else None
        rows.append(row)
    res = {"updated": now.isoformat(), "n_scored": len(rows), "enough_data": len(rows) >= MIN_PINS,
           "weights": {}, "patterns": [], "top10": [], "bottom10": []}
    if not res["enough_data"]:
        return res
    rows.sort(key=lambda r: (-r["score"], -r["clicks"]))
    top, bottom = rows[:10], rows[-10:]
    mean_all = sum(r["score"] for r in rows) / len(rows) or 1e-9
    for a in ATTRS:
        groups: dict[str, list[int]] = {}
        for r in rows:
            if r.get(a) is not None:
                groups.setdefault(str(r[a]), []).append(r["score"])
        w = {}
        for val, scores in groups.items():
            smoothed = (sum(scores) + PRIOR_N * mean_all) / (len(scores) + PRIOR_N)
            w[val] = round(min(CLIP[1], max(CLIP[0], smoothed / mean_all)), 3)
        res["weights"][a] = w
        tc = Counter(str(r.get(a)) for r in top if r.get(a) is not None)
        bc = Counter(str(r.get(a)) for r in bottom if r.get(a) is not None)
        for val in set(tc) | set(bc):
            diff = tc[val] - bc[val]
            if abs(diff) >= 3:     # e.g. 5 of top 10 vs 1 of bottom 10
                res["patterns"].append({"attr": a, "value": val, "top": tc[val], "bottom": bc[val],
                                        "weight": w.get(val, 1.0), "winner": diff > 0})
    res["patterns"].sort(key=lambda x: -abs(x["top"] - x["bottom"]))
    keep = ("pin_id", "title", "board", "kind", "hook_type", "layout", "palette", "saves", "clicks", "score")
    res["top10"] = [{k: r.get(k) for k in keep} for r in top]
    res["bottom10"] = [{k: r.get(k) for k in keep} for r in bottom]
    return res


def report_lines(L: dict, cfg: dict | None = None) -> list[str]:
    out = ["## What's working (top 10 vs bottom 10, by saves + 2 x site clicks)"]
    if not L.get("enough_data"):
        return out + [f"_Only {L.get('n_scored', 0)} pins are old enough (7+ days) with analytics. "
                      f"Patterns start at {MIN_PINS}; until then posting stays evenly mixed._", ""]
    pals = (cfg or load_config())["pin_design"]["palettes"]

    def name(a, v):
        if a == "palette" and v.isdigit() and int(v) < len(pals):
            return f"palette {v} (accent {pals[int(v)]['accent']})"
        return v
    if L["patterns"]:
        out += ["| Pattern | In top 10 | In bottom 10 | New weight |", "|---|---|---|---|"]
        for p in L["patterns"][:12]:
            icon = "WIN" if p["winner"] else "LOSE"
            out.append(f"| {icon}: {LABEL[p['attr']]} = **{name(p['attr'], p['value'])}** | {p['top']} | {p['bottom']} | x{p['weight']} |")
    else:
        out.append("_No attribute differs by 3+ pins between top and bottom 10 yet: no clear pattern, keep mixing._")
    out += ["", "**Top 10:** " + "; ".join(f"{r['title'][:40]} ({r['saves']} saves, {r['clicks']} clicks)" for r in L["top10"]),
            "", "**Bottom 10:** " + "; ".join(f"{r['title'][:40]} ({r['saves']}/{r['clicks']})" for r in L["bottom10"]),
            "", "_Next week the queue favours the winners (weights above), new recipes lean to winning boards, "
                "and new pin text leans to winning hook types. 25% of pins stay exploratory so losers can recover._", ""]
    return out


def weight(L: dict, attr: str, value) -> float:
    return float(((L or {}).get("weights", {}).get(attr) or {}).get(str(value), 1.0))


def load() -> dict:
    return read_json(LEARNINGS_FILE, {})


def run(posted=None, metrics=None, now=None) -> dict:
    posted = posted if posted is not None else read_json(POSTED_FILE, [])
    metrics = metrics if metrics is not None else read_json(METRICS_FILE, {})
    L = analyze(posted, metrics, now or now_utc())
    if L["enough_data"]:
        write_json(LEARNINGS_FILE, L)
    return L


def log2w(w: float) -> float:
    return math.log2(max(1e-6, w))
