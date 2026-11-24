"""The posting calendar.

queue.json holds one slot per planned post:
  {"slot": "2026-12-01#03", "due": "<UTC ISO>", "status": "planned|posted|failed",
   "item": {"slug", "board", "kind": "image|video", "variant"} | null, ...}

Slots are created a few days ahead (times are random inside the posting windows,
but stable once created). Content is assigned to a slot once and never shuffled,
so what gets posted is predictable and can be previewed.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from common import QUEUE_FILE, load_recipes, read_json, write_json

HORIZON_DAYS = 3


def _windows(cfg):
    out = []
    for w in cfg["schedule"]["windows"]:
        a, b = w.split("-")
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
        out.append((ah * 60 + am, bh * 60 + bm))
    return out


def pins_for_day(cfg, day_index: int) -> int:
    s = cfg["schedule"]
    if day_index >= s["total_days"]:
        return 0
    rnd = random.Random(f"count{day_index}")
    if day_index < s["ramp_days"]:
        rp = s["ramp_pins_per_day"]
        return rnd.randint(rp[0], rp[1]) if isinstance(rp, list) else rp
    base = s["steady_pins_per_day"]
    return max(3, min(5, base + rnd.choice([-1, 0, 0, 1])))   # 3-5/day, a bit irregular


def day_slots(cfg, d: date, n: int) -> list[datetime]:
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    rnd = random.Random(d.isoformat())
    wins = _windows(cfg)
    total = sum(b - a for a, b in wins)
    times = []
    for i in range(n):
        # spread evenly across the windows, jitter inside each share
        pos = (i + rnd.uniform(0.15, 0.85)) / n * total
        for a, b in wins:
            if pos <= b - a:
                m = int(a + pos)
                break
            pos -= b - a
        else:
            m = wins[-1][1] - 1
        times.append(datetime(d.year, d.month, d.day, m // 60, m % 60, rnd.randint(0, 59), tzinfo=tz))
    return [t.astimezone(ZoneInfo("UTC")) for t in sorted(times)]


def load_queue() -> dict:
    return read_json(QUEUE_FILE, {"start_date": None, "slots": []})


def all_items(cfg, recipes) -> list[dict]:
    items = []
    for r in recipes:
        for v in range(min(cfg["mix"]["pins_per_recipe"], len(r["pin_variants"]))):
            items.append({"slug": r["slug"], "board": r["board"], "kind": "image", "variant": v})
        if cfg["mix"]["video_share"] > 0 and r.get("video_script"):
            items.append({"slug": r["slug"], "board": r["board"], "kind": "video", "variant": 0})
    return items


def _key(it):
    return f"{it['slug']}:{it['kind']}:{it['variant']}"


def assign(cfg, q, recipes) -> int:
    created = {r["slug"]: r.get("created", "") for r in recipes}
    used = {_key(s["item"]) for s in q["slots"] if s.get("item")}
    pool = [it for it in all_items(cfg, recipes) if _key(it) not in used]
    if not pool:
        return 0
    gap = timedelta(days=cfg["mix"]["same_recipe_gap_days"])
    share = cfg["mix"]["video_share"]
    filled = 0
    assigned = [s for s in q["slots"] if s.get("item")]
    for slot in sorted(q["slots"], key=lambda s: s["due"]):
        if slot.get("item") or slot["status"] != "planned":
            continue
        if not pool:
            break
        due = datetime.fromisoformat(slot["due"])
        recent = [s for s in assigned if abs(datetime.fromisoformat(s["due"]) - due) < gap]
        recent_slugs = {s["item"]["slug"] for s in recent}
        n_video = sum(1 for s in assigned if s["item"]["kind"] == "video")
        want_video = assigned and n_video / len(assigned) < share
        last_boards = [s["item"]["board"] for s in sorted(assigned, key=lambda s: s["due"])[-len(cfg["boards"]):]]
        per_slug = {}
        for s in assigned:
            per_slug[s["item"]["slug"]] = per_slug.get(s["item"]["slug"], 0) + 1

        def score(it):
            return (
                it["slug"] in recent_slugs,                        # avoid repeats within the gap
                (it["kind"] == "video") != bool(want_video),       # keep the video share
                last_boards[::-1].index(it["board"]) * -1 if it["board"] in last_boards else -99,  # rotate boards
                per_slug.get(it["slug"], 0),                       # spread across recipes
                created.get(it["slug"], ""),                       # older recipes first
                it["variant"],
            )
        best = min(pool, key=score)
        pool.remove(best)
        slot["item"] = best
        assigned.append(slot)
        filled += 1
    return filled


def plan(cfg, now: datetime, persist: bool = True, horizon: int = HORIZON_DAYS) -> dict:
    q = load_queue()
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    today = now.astimezone(tz).date()
    if not q.get("start_date"):
        q["start_date"] = (cfg["schedule"].get("start_date") or today.isoformat())
        if isinstance(q["start_date"], date):
            q["start_date"] = q["start_date"].isoformat()
    start = date.fromisoformat(q["start_date"])
    have = {s["slot"].split("#")[0] for s in q["slots"]}
    d = max(start, today)
    while d <= today + timedelta(days=horizon):
        idx = (d - start).days
        if d.isoformat() not in have:
            for i, t in enumerate(day_slots(cfg, d, pins_for_day(cfg, idx))):
                q["slots"].append({"slot": f"{d.isoformat()}#{i:02d}", "due": t.isoformat(),
                                   "status": "planned", "item": None, "attempts": 0})
        d += timedelta(days=1)
    q["slots"].sort(key=lambda s: s["due"])
    assign(cfg, q, load_recipes())
    if persist:
        write_json(QUEUE_FILE, q)
    return q


def content_runway_days(cfg, q, recipes) -> float:
    """How many days of posting the unused content covers at the steady rate."""
    used = {_key(s["item"]) for s in q["slots"] if s.get("item")}
    left = sum(1 for it in all_items(cfg, recipes) if _key(it) not in used)
    return left / max(1, cfg["schedule"]["steady_pins_per_day"])
