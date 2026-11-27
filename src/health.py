"""Health check for the whole Pinterest autopilot.

Runs every 6 hours on GitHub (health.yml, works with the Mac off) and from the
Claude check task on the Mac (`--local` adds the Mac video helper). Writes
data/state/health.json:

  {"checked": ..., "ok": bool, "issues": [
     {"id", "who": "auto" | "you", "title", "detail", "fix"}]}

who = "auto": something Claude can fix in the code/workflows by itself.
who = "you":  needs you (a login, a key, a decision). Only these are emailed
              (plus "auto" issues Claude could not fix).

  python src/health.py            # print + write health.json
  python src/health.py --local    # also check the Mac helper (on the Mac)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from common import POSTED_FILE, QUEUE_FILE, STATE_DIR, load_config, load_recipes, log, now_utc, read_json, write_json

HEALTH_FILE = STATE_DIR / "health.json"
VIDEOS_FILE = STATE_DIR / "videos.json"
PINTEREST_USER = "theeasyproteinkitchen"
WORKFLOWS = {"post.yml": "Post due pins (hourly)", "content.yml": "New recipes (twice a day)",
             "pages.yml": "Website build", "video.yml": "Weekly videos", "report.yml": "Weekly report"}


def gh_runs(workflow: str, n: int = 5) -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY") or "subuwithasuu/subuwithasuu.github.io"
    out = subprocess.run(["gh", "run", "list", "--repo", repo, "--workflow", workflow, "-L", str(n),
                          "--json", "status,conclusion,createdAt,event,databaseId"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout or "[]")


def run_checks(cfg: dict, local: bool) -> list[dict]:
    now = now_utc()
    issues: list[dict] = []

    def add(id_, who, title, detail, fix):
        issues.append({"id": id_, "who": who, "title": title, "detail": detail, "fix": fix})

    def guard(name, fn):
        try:
            fn()
        except Exception as e:                       # a broken check is itself worth a look
            add(f"check-{name}", "auto", f"Health check '{name}' crashed", str(e)[:300],
                "Claude: look at src/health.py")

    base = cfg["site"]["base_url"]
    posted = read_json(POSTED_FILE, [])
    q = read_json(QUEUE_FILE, {"slots": [], "start_date": None})

    def site():
        from post_due import check_live_site
        p = check_live_site(cfg)
        if p:
            add("site-down", "auto", "Website is not reachable", p,
                "Claude: re-run the 'Build & deploy site' workflow; check GitHub Pages settings")

    def feeds():
        bad = []
        for b in cfg["boards"]:
            from common import board_slug
            url = f"{base}/feeds/{board_slug(b['name'])}.xml"
            r = requests.get(url, timeout=20)
            if r.status_code != 200 or "<rss" not in r.text:
                bad.append(f"{url} -> HTTP {r.status_code}")
        if bad:
            add("feeds", "auto", "RSS feeds broken", "; ".join(bad), "Claude: rebuild the site, check build_site.write_feeds")

    def posting():
        rss = [p for p in posted if p.get("via") == "rss"]
        if not rss or not q.get("start_date"):
            return
        last = max(datetime.fromisoformat(p["posted_at"]) for p in rss)
        day = (now.date() - datetime.fromisoformat(q["start_date"]).date()).days
        if day < cfg["schedule"]["total_days"] and now - last > timedelta(hours=30):
            add("posting-stalled", "auto", "No new pins added to the feeds for 30+ hours",
                f"last pin {last:%Y-%m-%d %H:%M} UTC", "Claude: check the 'Post due pins' workflow runs")

    def pinterest_import():
        """Is Pinterest actually turning our feed items into pins? (public profile feed)"""
        r = requests.get(f"https://www.pinterest.com/{PINTEREST_USER}/feed.rss",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"profile feed HTTP {r.status_code}")
        dates = [parsedate_to_datetime(d) for d in re.findall(r"<pubDate>([^<]+)</pubDate>", r.text)]
        newest = max(dates) if dates else None
        old_enough = [p for p in posted if p.get("via") == "rss"
                      and now - datetime.fromisoformat(p["posted_at"]) > timedelta(hours=48)]
        recent_feed = [p for p in posted if p.get("via") == "rss"
                       and now - datetime.fromisoformat(p["posted_at"]) < timedelta(hours=72)]
        if old_enough and recent_feed and (newest is None or now - newest > timedelta(hours=72)):
            add("pinterest-not-importing", "you", "Pinterest stopped creating pins from the RSS feeds",
                f"newest pin on the profile: {newest:%Y-%m-%d} UTC" if newest else "profile shows no pins",
                "Open Pinterest > Settings > Create Pins in bulk: are all 8 feeds still listed? "
                "Also check your email for a message from Pinterest about the feeds or the account.")

    def workflows():
        for wf, name in WORKFLOWS.items():
            runs = [r for r in gh_runs(wf, 6) if r["status"] == "completed"]
            fails = 0
            for r in runs:
                if r["conclusion"] in ("failure", "timed_out", "startup_failure"):
                    fails += 1
                else:
                    break
            if fails >= (3 if wf == "post.yml" else 2):
                add(f"wf-{wf}", "auto", f"'{name}' failed {fails} times in a row",
                    f"latest run id {runs[0]['databaseId']}", f"Claude: read the log (gh run view {runs[0]['databaseId']} --log-failed) and fix")
        recent = gh_runs("post.yml", 1)
        if recent and now - datetime.fromisoformat(recent[0]["createdAt"].replace("Z", "+00:00")) > timedelta(hours=26):
            add("wf-disabled", "auto", "The hourly posting workflow has not run for over a day",
                f"last run {recent[0]['createdAt']}", "Claude: check it is enabled (gh workflow enable post.yml)")

    def runway():
        from queue_plan import content_runway_days
        days = content_runway_days(cfg, q, load_recipes())
        if days < 7:
            add("content-low", "auto", f"Only ~{days:.0f} days of unposted content left",
                "new recipes are not being generated fast enough", "Claude: check the 'Content' workflow and Gemini quota")

    def gemini_key():
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key}, timeout=20)
        if r.status_code in (400, 401, 403):
            add("gemini-key", "you", "The Gemini API key stopped working", f"HTTP {r.status_code}",
                "Create a new key at aistudio.google.com/apikey, then run in Terminal: "
                "gh secret set GEMINI_API_KEY --repo subuwithasuu/subuwithasuu.github.io")

    def goatcounter():
        tok = os.environ.get("GOATCOUNTER_TOKEN")
        if not tok or not cfg["site"].get("goatcounter"):
            return
        r = requests.get(f"https://{cfg['site']['goatcounter']}.goatcounter.com/api/v0/me",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code in (401, 403):
            add("goatcounter-key", "you", "The GoatCounter key stopped working", f"HTTP {r.status_code}",
                "Create a new key (GoatCounter > your name > API, tick Read statistics), then run: "
                "gh secret set GOATCOUNTER_TOKEN --repo subuwithasuu/subuwithasuu.github.io")

    def videos():
        pool = read_json(VIDEOS_FILE, [])
        if not pool:
            return
        ups = [datetime.fromisoformat(v["uploaded_at"]) for v in pool if v.get("uploaded_at")]
        last = max(ups) if ups else None
        waiting = sum(v["status"] == "ready" for v in pool)
        mac = read_json(STATE_DIR / "mac_status.json", {})
        if mac.get("pinterest_login") == "logged_out":
            add("mac-pinterest-login", "you", "The video helper on your Mac is logged out of Pinterest",
                f"since {mac.get('updated', '?')}",
                "Log in to your Mac; a Pinterest login window opens by itself. Log in there once "
                "(the window with 'Chrome is being controlled by automated test software').")
        elif waiting and (last is None or now - last > timedelta(days=9)):
            add("videos-waiting", "you", f"{waiting} video pins are waiting to be uploaded",
                f"last upload {last:%Y-%m-%d}" if last else "never uploaded",
                "Log in to your Mac (with internet). The helper uploads them by itself within the hour.")

    def schedule_end():
        if not q.get("start_date"):
            return
        left = cfg["schedule"]["total_days"] - (now.date() - datetime.fromisoformat(q["start_date"]).date()).days
        if 0 <= left <= 10:
            add("schedule-ending", "you", f"The posting plan ends in {left} days",
                f"started {q['start_date']}, runs {cfg['schedule']['total_days']} days",
                "Tell Claude whether to keep posting (and for how long).")

    def mac_helper():
        log_file = Path.home() / "Library/Logs/easyprotein-video-upload.log"
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.easyproteinkitchen.videoupload"],
                             capture_output=True, text=True)
        if out.returncode:
            add("mac-helper-missing", "auto", "The Mac video helper is not installed/running",
                out.stderr.strip()[:200], "Claude: run scripts/install_mac_uploader.sh")
        if log_file.exists():
            tail = log_file.read_text(errors="ignore").splitlines()[-40:]
            errs = [l for l in tail if "Traceback" in l or "failed" in l.lower()]
            if errs:
                add("mac-helper-errors", "auto", "The Mac video helper logged errors",
                    " | ".join(errs[-3:])[:400], f"Claude: read {log_file}")

    for name, fn in [("site", site), ("feeds", feeds), ("posting", posting), ("pinterest", pinterest_import),
                     ("workflows", workflows), ("runway", runway), ("gemini", gemini_key),
                     ("goatcounter", goatcounter), ("videos", videos), ("schedule", schedule_end)]:
        guard(name, fn)
    if local:
        guard("mac", mac_helper)
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    issues = run_checks(cfg, a.local)
    res = {"checked": now_utc().isoformat(), "where": "mac" if a.local else "github",
           "ok": not issues, "issues": issues}
    if not a.no_write:
        write_json(HEALTH_FILE, res)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
