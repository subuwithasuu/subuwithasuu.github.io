"""Mac side of the weekly video pins: runs by itself after you log in to your Mac
(launchd agent, see scripts/install_mac_uploader.sh), then once an hour while the
Mac is on. It does nothing unless a week has passed since the last upload.

  1. git pull, read data/state/videos.json, take the "ready" videos whose files are
     live on the site
  2. write a Pinterest bulk .csv: one row per video, publish dates spread over the
     coming week (2 a day by default)
  3. open Chrome with its own Pinterest profile (you log in there once; your
     password is never stored by this script), upload the .csv on
     Settings > Create Pins in bulk
  4. mark them uploaded, commit + push, show a Mac notification

  .venv/bin/python scripts/mac_video_upload.py            # normal (respects the weekly gap)
  .venv/bin/python scripts/mac_video_upload.py --force    # upload now
  .venv/bin/python scripts/mac_video_upload.py --dry-run  # write the .csv only
  .venv/bin/python scripts/mac_video_upload.py --login    # open the login window by hand (normally automatic)
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from common import POSTED_FILE, load_config, read_json, write_json  # noqa: E402

HOME = Path.home() / "Library" / "Application Support" / "EasyProteinVideoUploader"
PROFILE = HOME / "chrome-profile"
STATE = HOME / "state.json"
VIDEOS_FILE = ROOT / "data" / "state" / "videos.json"
UPLOAD_PAGE = "https://www.pinterest.com/settings/bulk-create-pins/"


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def notify(msg: str) -> None:
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Easy Protein Kitchen"'],
                   check=False)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          check=True).stdout


def wait_for_network(minutes: int = 15) -> bool:
    for _ in range(minutes * 2):
        try:
            requests.head("https://github.com", timeout=10)
            return True
        except requests.RequestException:
            time.sleep(30)
    return False


def schedule(n: int, times: list[str], now: datetime) -> list[datetime]:
    """Next n publish slots at the given UTC times, starting at least 2 hours from now."""
    out, day = [], now.date()
    while len(out) < n:
        for t in sorted(times):
            hh, mm = map(int, t.split(":"))
            when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)
            if when >= now + timedelta(hours=2) and len(out) < n:
                out.append(when)
        day += timedelta(days=1)
    return out


def open_browser(p, visible: bool = True):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=False, viewport={"width": 1280, "height": 900},
        args=[] if visible else ["--window-position=-2000,0"])


def upload(csv_path: Path) -> tuple[bool, str]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(UPLOAD_PAGE, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)
        if "login" in page.url or page.locator("input[type=file]").count() == 0:
            ctx.close()
            return False, "LOGIN"
        inputs = page.locator("input[type=file]")
        target = inputs.first
        for i in range(inputs.count()):
            if "csv" in (inputs.nth(i).get_attribute("accept") or ""):
                target = inputs.nth(i)
                break
        target.set_input_files(str(csv_path))
        page.wait_for_timeout(20_000)
        text = page.inner_text("body")
        shot = HOME / f"upload-{datetime.now():%Y%m%d-%H%M}.png"
        page.screenshot(path=str(shot))
        ctx.close()
    if "upload successful" in text.lower():        # Pinterest's confirmation dialog
        return True, f"Pinterest: upload successful ({shot})"
    return False, f"no success message, see {shot}"


def report_status(login: str) -> None:
    """Tell the GitHub health check whether the helper is logged in to Pinterest.
    Only pushes when the state changes, so the repo isn't spammed."""
    f = ROOT / "data" / "state" / "mac_status.json"
    old = read_json(f, {})
    if old.get("pinterest_login") == login:
        return
    write_json(f, {"pinterest_login": login, "updated": datetime.now(timezone.utc).isoformat()})
    try:
        git("add", str(f))
        git("-c", "user.name=subuwithasuu", "-c", "user.email=subuwithasuu@users.noreply.github.com",
            "commit", "-q", "-m", f"mac helper: pinterest {login}")
        git("pull", "-q", "--rebase", "--autostash")
        git("push", "-q")
    except subprocess.CalledProcessError as e:
        log(f"status push failed: {e.stderr}")


def login_flow() -> bool:
    """Open the helper's Chrome at Pinterest's login page and wait (up to 30 min)
    until you have logged in; the window then closes by itself."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.pinterest.com/login/")
        page.bring_to_front()
        log("waiting for Pinterest login in the helper window")
        ok = False
        for _ in range(900):
            time.sleep(2)
            try:
                cookies = ctx.cookies()
            except Exception:                          # window was closed
                break
            if any(c["name"] == "_auth" and c["value"] == "1" for c in cookies):
                ok = True
                time.sleep(3)
                break
        try:
            ctx.close()
        except Exception:
            pass
    log("logged in" if ok else "login not finished")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--login", action="store_true")
    a = ap.parse_args()
    HOME.mkdir(parents=True, exist_ok=True)
    lock = open(HOME / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                        # another run is busy

    if a.login:
        return 0 if login_flow() else 1

    cfg = load_config()
    vb = cfg["video_batch"]
    state = read_json(STATE, {})
    now = datetime.now(timezone.utc)
    last = state.get("last_upload")
    if last and not (a.force or a.dry_run) and now - datetime.fromisoformat(last) < timedelta(days=vb["upload_every_days"]):
        return 0                                        # uploaded this week already
    if not wait_for_network():
        log("no network")
        return 1
    try:
        git("pull", "-q", "--rebase", "--autostash")
    except subprocess.CalledProcessError as e:
        log(f"git pull failed: {e.stderr}")
        return 1

    pool = read_json(VIDEOS_FILE, [])
    base = cfg["site"]["base_url"]
    ready = []
    for v in pool:
        if v["status"] != "ready":
            continue
        try:
            ok = requests.head(f"{base}/videos/{v['file']}", timeout=20, allow_redirects=True).status_code == 200
        except requests.RequestException:
            ok = False
        if ok:
            ready.append(v)
        else:
            log(f"not live yet: {v['file']}")
    ready = ready[:min(200, vb["per_week"] * 2)]
    if not ready:
        log("no ready videos")
        return 0

    slots = schedule(len(ready), vb["post_times_utc"], now)
    csv_path = HOME / f"video-pins-{now:%Y%m%d}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Media URL", "Pinterest board", "Thumbnail", "Description", "Link",
                    "Publish date", "Keywords"])
        for v, when in zip(ready, slots):
            w.writerow([v["title"], f"{base}/videos/{v['file']}", v["board"], "00:01", v["description"],
                        v["link"], when.strftime("%Y-%m-%dT%H:%M:%S"), v["keywords"]])
    log(f"{len(ready)} videos -> {csv_path}")
    if a.dry_run:
        return 0

    ok, info = upload(csv_path)
    if info == "LOGIN":
        # Pinterest logged the helper out: open the login window by itself (at most
        # every 12 hours, so an ignored window doesn't keep popping up).
        last_ask = state.get("last_login_prompt")
        if last_ask and now - datetime.fromisoformat(last_ask) < timedelta(hours=12) and not a.force:
            return 2
        state["last_login_prompt"] = now.isoformat()
        write_json(STATE, state)
        notify("Pinterest logged out the video helper. Please log in in the window that just opened.")
        report_status("logged_out")
        if not login_flow():
            return 2
        ok, info = upload(csv_path)
        if info == "LOGIN":
            return 2
    log(info)
    if not ok:
        notify("Video pin upload had a problem. Open the uploader log.")
        return 1

    report_status("ok")
    stamp = now.isoformat()
    posted = read_json(POSTED_FILE, [])
    ids = {v["id"]: when for v, when in zip(ready, slots)}
    for v in pool:
        if v["id"] in ids:
            v.update(status="uploaded", uploaded_at=stamp, publish_at=ids[v["id"]].isoformat())
            posted.append({"pin_id": v["id"], "via": "csv", "kind": "video", "slug": v["slug"],
                           "board": v["board"], "title": v["title"], "link": v["link"],
                           "variant": v.get("variant"), "posted_at": ids[v["id"]].isoformat()})
    write_json(VIDEOS_FILE, pool)
    write_json(POSTED_FILE, posted)
    state["last_upload"] = stamp
    write_json(STATE, state)
    try:
        git("add", "data/state/videos.json", "data/state/posted.json")
        git("-c", "user.name=subuwithasuu", "-c", "user.email=subuwithasuu@users.noreply.github.com",
            "commit", "-q", "-m", f"video pins uploaded {now:%F}")
        git("pull", "-q", "--rebase")
        git("push", "-q")
    except subprocess.CalledProcessError as e:
        log(f"git push failed (will retry next run): {e.stderr}")
    notify(f"{len(ready)} video pins scheduled on Pinterest for the coming week.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
