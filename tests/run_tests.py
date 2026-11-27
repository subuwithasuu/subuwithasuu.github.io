"""Offline tests (no API keys needed). Run from a scratch COPY of the repo:
    python tests/run_tests.py
They write sample recipes into data/, so never run them in the real repo."""
from __future__ import annotations

import copy
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import QUEUE_FILE, RECIPES_DIR, ROOT, load_config, read_json, write_json  # noqa: E402
from validate import validate  # noqa: E402

cfg = load_config()
base = read_json(ROOT / "tests/sample_recipe.json", None)
fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += not cond


# --- validator -------------------------------------------------------------
check("sample recipe valid", validate(base)[0] == [])
bad = copy.deepcopy(base); bad["ingredients"][-1].update(qty="3", unit="tbsp")
check("catches too much salt", any("salt" in e for e in validate(bad)[0]))
bad = copy.deepcopy(base); bad["steps"][0] += " Bake at 900°F."
check("catches impossible oven temp", any("temperature" in e for e in validate(bad)[0]))
bad = copy.deepcopy(base); bad["ingredients"][1]["item"] = "boneless chicken thighs"
check("requires poultry 165F", any("165" in e for e in validate(bad)[0]))
bad = copy.deepcopy(base); bad["total_minutes"] = 10
check("catches times not adding up", any("total time" in e for e in validate(bad)[0]))
bad = copy.deepcopy(base); bad["ingredients"][0]["qty"] = "some"
check("catches missing quantity", any("quantity" in e for e in validate(bad)[0]))
bad = copy.deepcopy(base); bad["description"] += " It cures headaches."
check("blocks health claims", any("claim" in e for e in validate(bad)[0]))

# --- protein rules -----------------------------------------------------------
from nutrition import check_protein  # noqa: E402
B = {b["name"]: b for b in cfg["boards"]}
ok = copy.deepcopy(base)
check("sample protein verified", check_protein(ok, cfg, B[ok["board"]]) == [] and ok["protein_g"] == 32)
bad = copy.deepcopy(base); bad["protein_g_per_serving"] = 50
check("rejects protein overclaim (50g claimed)", any("overclaimed" in e for e in check_protein(bad, cfg, B[bad["board"]])))
bad = copy.deepcopy(base); bad["pin_variants"][1]["description"] = "Packed with 45g protein!"
check_protein(bad, cfg, B[bad["board"]])
check("rewrites inflated protein number in pin text", "32g protein" in bad["pin_variants"][1]["description"])
bad = copy.deepcopy(base)
check("vegetarian board rejects shrimp", any("vegetarian" in e for e in check_protein(bad, cfg, B["High Protein Vegetarian"])))
low = copy.deepcopy(base); low["ingredients"] = [i for i in low["ingredients"] if "shrimp" not in i["item"]]; low["protein_g_per_serving"] = 19; low["servings"] = 2   # ~19.8 g floor
check("rejects low-protein meal", any("only" in e for e in check_protein(low, cfg, B["High Protein Dinners"])))
check("snack board allows 10g+", not any("only" in e for e in check_protein(copy.deepcopy(low), cfg, B["High Protein Snacks"])))

# --- live-site guard (simulated responses, no real network) -----------------
import requests  # noqa: E402
import post_due  # noqa: E402


class FakeResp:
    def __init__(self, url, status=200, text=""):
        self.url, self.status_code, self.text = url, status, text


def guard_with(resp_or_exc):
    real = requests.get
    def fake(*a, **k):
        if isinstance(resp_or_exc, Exception):
            raise resp_or_exc
        return resp_or_exc
    requests.get = fake
    try:
        return post_due.check_live_site(cfg)
    finally:
        requests.get = real


base_url = cfg["site"]["base_url"]
name = cfg["site"]["name"]
check("guard passes when site is live at final address",
      guard_with(FakeResp(f"{base_url}/privacy/", 200, f"<h1>{name}</h1>")) is None)
check("guard blocks when domain unreachable",
      guard_with(requests.ConnectionError("dns")) is not None)
check("guard blocks redirect to another site",
      guard_with(FakeResp("https://www.namecheap.com/parked/", 200, name)) is not None)
check("guard blocks 404 (DNS points at someone else / not deployed)",
      guard_with(FakeResp(f"{base_url}/privacy/", 404, "")) is not None)
check("guard blocks a parked-domain page", guard_with(FakeResp(f"{base_url}/privacy/", 200, "This domain is for sale")) is not None)

# --- fake library: 80 recipes over 8 boards ---------------------------------
for f in RECIPES_DIR.glob("*.json"):
    f.unlink()
for i in range(80):
    r = copy.deepcopy(base)
    r["slug"] = f"test-recipe-{i:02d}"
    r["title"] = f"Test Recipe {i}"
    r["board"] = cfg["boards"][i % 8]["name"]
    r["created"] = f"2026-11-{1 + i % 28:02d}"
    write_json(RECIPES_DIR / f"{r['slug']}.json", r)

# --- simulate 105 days of hourly runs (planning only) ------------------------
if QUEUE_FILE.exists():
    QUEUE_FILE.unlink()
import queue_plan  # noqa: E402
start = datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc)
t = start
while t < start + timedelta(days=cfg["schedule"]["total_days"] + 5):
    q = queue_plan.plan(cfg, t)
    for s in q["slots"]:                     # pretend everything due got posted
        if s["status"] == "planned" and s["item"] and s["due"] <= t.isoformat():
            s["status"] = "posted"
    write_json(QUEUE_FILE, q)
    t += timedelta(hours=6)
q = read_json(QUEUE_FILE, None)
posted = [s for s in q["slots"] if s["status"] == "posted"]
per_day = Counter(s["slot"].split("#")[0] for s in q["slots"])
days = sorted(per_day)
ramp = [per_day[d] for d in days[:24]]
print(f"   ramp pins/day: {ramp}")
check("ramp days have 10-12 pins", all(10 <= n <= 12 for n in ramp))
check(f"steady days 3-5 slots (day 30: {per_day[days[30]]})", 3 <= per_day[days[30]] <= 5)
check(f"stops after {cfg['schedule']['total_days']} days ({len(days)} days planned)", len(days) == cfg["schedule"]["total_days"])
boards = Counter(s["item"]["board"] for s in posted)
print("   pins per board:", dict(boards))
ramp_boards = Counter(s["item"]["board"] for s in posted if s["slot"] < days[24])
print("   pins per board after ramp:", dict(ramp_boards))
check("ramp gives every board 30+ pins", all(c >= 30 for c in ramp_boards.values()))
vid = sum(1 for s in posted if s["item"]["kind"] == "video") / len(posted)
want_vid = cfg["mix"]["video_share"]
if want_vid:
    check(f"video share ~{want_vid:.0%} (got {vid:.0%})", abs(vid - want_vid) <= 0.08)
else:
    check(f"RSS mode: no video pins scheduled (got {vid:.0%})", vid == 0)
keys = [f"{s['item']['slug']}:{s['item']['kind']}:{s['item']['variant']}" for s in posted]
check("no pin posted twice", len(keys) == len(set(keys)))
# same recipe gap
by_slug = {}
close = 0
for s in sorted(posted, key=lambda s: s["due"]):
    d = datetime.fromisoformat(s["due"])
    if s["item"]["slug"] in by_slug and d - by_slug[s["item"]["slug"]] < timedelta(days=1):
        close += 1
    by_slug[s["item"]["slug"]] = d
check(f"same recipe rarely within 24h ({close} cases)", close <= len(posted) * 0.02)
unfilled = [s for s in q["slots"] if not s["item"]]
print(f"   total slots {len(q['slots'])}, posted {len(posted)}, unfilled (ran out of content) {len(unfilled)}")
print("   (test library is fixed at 80 recipes; the daily content job keeps adding more)")

# --- feedback loop ------------------------------------------------------------
import learn  # noqa: E402
from hooks import winners  # noqa: E402
rng = __import__("random").Random(7)
now = datetime(2027, 2, 1, tzinfo=timezone.utc)
fake_posted, fake_metrics = [], {}
for i in range(60):
    hook = ["number", "outcome", "teacher", "speed"][i % 4]
    layout = ["split", "single", "band"][i % 3]
    board = cfg["boards"][i % 8]["name"]
    pid = f"p{i}"
    fake_posted.append({"pin_id": pid, "posted_at": (now - timedelta(days=10 + i % 20)).isoformat(),
                        "title": f"pin {i}", "board": board, "kind": "image", "hook_type": hook,
                        "layout": layout, "palette": i % 5, "protein_g": 30 + i % 15, "total_minutes": 20})
    s = 2 + rng.randint(0, 3) + (12 if hook == "teacher" else 0) + (6 if layout == "single" else 0) \
        + (5 if board == "High Protein Dinners" else 0)
    fake_metrics[pid] = {"SAVE": s, "OUTBOUND_CLICK": s // 3}
fake_posted.append({"pin_id": "young", "posted_at": (now - timedelta(days=2)).isoformat(), "title": "new",
                    "board": "High Protein Snacks", "kind": "image", "hook_type": "budget", "layout": "band", "palette": 0})
fake_metrics["young"] = {"SAVE": 999, "OUTBOUND_CLICK": 999}
L = learn.analyze(fake_posted, fake_metrics, now)
check("learn: ignores pins younger than 7 days", all(r["pin_id"] != "young" for r in L["top10"]))
check("learn: finds teacher hooks win", learn.weight(L, "hook_type", "teacher") > 1.3)
check("learn: finds single layout wins", learn.weight(L, "layout", "single") > learn.weight(L, "layout", "band"))
check("learn: weights clipped 0.5-2.0", all(0.5 <= w <= 2.0 for a in L["weights"].values() for w in a.values()))
pat = {(p["attr"], p["value"]) for p in L["patterns"] if p["winner"]}
check("learn: lists hook_type=teacher as a top-10 pattern", ("hook_type", "teacher") in pat)
check("learn: says 'not enough data' below 20 pins",
      not learn.analyze(fake_posted[:15], fake_metrics, now)["enough_data"])
print("   report preview:\n   " + "\n   ".join(learn.report_lines(L, cfg)[:6]))
write_json(learn.LEARNINGS_FILE, L)
check("hooks: prompt asks for winning hook types", winners()[:1] == ["teacher"])

# queue follows the winners: compare 2 weeks of slots with vs without learnings
def share_after(with_learnings: bool):
    if not with_learnings:
        learn.LEARNINGS_FILE.unlink()
    else:
        write_json(learn.LEARNINGS_FILE, L)
    for r_ in RECIPES_DIR.glob("*.json"):           # give the test recipes hook types
        rr = read_json(r_, None)
        for v_, t_ in zip(rr["pin_variants"], ["number", "outcome", "teacher", "speed"]):
            v_["hook_type"] = t_
        write_json(r_, rr)
    if QUEUE_FILE.exists():
        QUEUE_FILE.unlink()
    t_ = datetime(2026, 12, 1, 5, tzinfo=timezone.utc)
    q_ = None
    for _ in range(14):
        q_ = queue_plan.plan(cfg, t_)
        t_ += timedelta(days=1)
    items = [s["item"] for s in q_["slots"] if s.get("item")]
    img = [it for it in items if it["kind"] == "image"]
    return (sum(it["layout"] == "single" for it in img) / len(img),
            sum(it.get("hook_type") == "teacher" for it in items) / len(items))
base_layout, base_hook = share_after(False)
win_layout, win_hook = share_after(True)
print(f"   single layout share {base_layout:.0%} -> {win_layout:.0%}; teacher-hook share {base_hook:.0%} -> {win_hook:.0%}")
check("queue: posts more of the winning layout", win_layout > base_layout + 0.05)
check("queue: posts more of the winning hook type", win_hook > base_hook + 0.03)
check("queue: still explores other layouts", win_layout < 0.9)

print("\nALL PASSED" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
