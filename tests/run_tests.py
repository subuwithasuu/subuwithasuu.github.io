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
check("guard blocks redirect to github.io",
      guard_with(FakeResp("https://subuwithasuu.github.io/easy-protein-kitchen/privacy/", 200, name)) is not None)
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
while t < start + timedelta(days=110):
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
check(f"stops after 105 days ({len(days)} days planned)", len(days) == 105)
boards = Counter(s["item"]["board"] for s in posted)
print("   pins per board:", dict(boards))
ramp_boards = Counter(s["item"]["board"] for s in posted if s["slot"] < days[24])
print("   pins per board after ramp:", dict(ramp_boards))
check("ramp gives every board 30+ pins", all(c >= 30 for c in ramp_boards.values()))
vid = sum(1 for s in posted if s["item"]["kind"] == "video") / len(posted)
check(f"video share ~25% (got {vid:.0%})", 0.18 <= vid <= 0.32)
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

print("\nALL PASSED" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
